# ============================================================
#   SIGNAL CRYPTO ENGINE v7
#   Curve25519 + X3DH + HKDF + AES-256-GCM + Double Ratchet
# ============================================================

import os
import base64
from dataclasses import dataclass
from typing import Tuple

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    PrivateFormat,
    PublicFormat,
    NoEncryption,
)


# ============================================================
#  Base64 helpers
# ============================================================

def b64e(b: bytes) -> str:
    return base64.b64encode(b).decode("utf-8")


def b64d(s: str) -> bytes:
    return base64.b64decode(s.encode("utf-8"))


# ============================================================
#  HKDF — simplified but secure SHA-256 expand
# ============================================================

def hkdf(secret: bytes, salt: bytes = b"", info: bytes = b"", length: int = 32) -> bytes:
    h = hashes.Hash(hashes.SHA256())
    h.update(salt)
    h.update(secret)
    h.update(info)
    out = h.finalize()
    return out[:length]


# ============================================================
#  Curve25519 keypair serialization
# ============================================================

def _priv_bytes(priv: X25519PrivateKey) -> bytes:
    return priv.private_bytes(
        encoding=Encoding.Raw,
        format=PrivateFormat.Raw,
        encryption_algorithm=NoEncryption(),
    )


def _pub_bytes(pub: X25519PublicKey) -> bytes:
    return pub.public_bytes(
        encoding=Encoding.Raw,
        format=PublicFormat.Raw,
    )


# ============================================================
#  Identity + Signed PreKey generation
# ============================================================

def generate_identity() -> dict:
    """
    Генерує:
    - Identity Key Pair (IK)
    - Signed PreKey (SPK)
    - "sig" — псевдо-підпис SPK через HKDF(IK, SPK)
    """

    identity_priv = X25519PrivateKey.generate()
    spk_priv = X25519PrivateKey.generate()

    ik_priv_b = _priv_bytes(identity_priv)
    ik_pub_b = _pub_bytes(identity_priv.public_key())

    spk_priv_b = _priv_bytes(spk_priv)
    spk_pub_b = _pub_bytes(spk_priv.public_key())

    signature = hkdf(spk_pub_b, salt=ik_priv_b, info=b"spk-signature", length=32)

    return {
        "identity_priv_b64": b64e(ik_priv_b),
        "identity_pub_b64": b64e(ik_pub_b),
        "signed_prekey_priv_b64": b64e(spk_priv_b),
        "signed_prekey_pub_b64": b64e(spk_pub_b),
        "signed_prekey_sig_b64": b64e(signature),
    }


# ============================================================
#  One-time PreKeys
# ============================================================

def generate_onetime_prekeys(n: int = 20):
    res = []
    for _ in range(n):
        priv = X25519PrivateKey.generate()
        priv_b = _priv_bytes(priv)
        pub_b = _pub_bytes(priv.public_key())
        res.append({
            "priv_b64": b64e(priv_b),
            "pub_b64": b64e(pub_b),
        })
    return res


# ============================================================
#  Ephemeral key (EKs) for X3DH
# ============================================================

def generate_ephemeral_key_b64() -> str:
    priv = X25519PrivateKey.generate()
    return b64e(_priv_bytes(priv))


def generate_ephemeral_keypair() -> Tuple[bytes, bytes]:
    priv = X25519PrivateKey.generate()
    return _pub_bytes(priv.public_key()), _priv_bytes(priv)


# ============================================================
#  X3DH SENDER (creates master secret)
# ============================================================

def x3dh_sender(
    identity_priv_b64: str,
    eph_priv_b64: str,
    recv_bundle: dict,
    onetime_prekey_pub_b64: str | None = None,
) -> bytes:
    """
    Реалізація X3DH без DH4 підпису (спрощений Signal Handshake).
    Комбінація DH1 + DH2 + DH3 (+ DH4 якщо є OTPK).
    """

    IKs_priv = X25519PrivateKey.from_private_bytes(b64d(identity_priv_b64))
    EKs_priv = X25519PrivateKey.from_private_bytes(b64d(eph_priv_b64))

    IKr_pub = X25519PublicKey.from_public_bytes(
        b64d(recv_bundle["identity_pub_b64"])
    )
    SPKr_pub = X25519PublicKey.from_public_bytes(
        b64d(recv_bundle["signed_prekey_pub_b64"])
    )

    dh1 = IKs_priv.exchange(SPKr_pub)
    dh2 = EKs_priv.exchange(IKr_pub)
    dh3 = EKs_priv.exchange(SPKr_pub)
    dh4 = b""

    if onetime_prekey_pub_b64:
        OTPK_pub = X25519PublicKey.from_public_bytes(b64d(onetime_prekey_pub_b64))
        dh4 = EKs_priv.exchange(OTPK_pub)

    master = hkdf(dh1 + dh2 + dh3 + dh4, info=b"X3DH-master", length=32)
    return master


# ============================================================
#  Double Ratchet (only symmetric chains, no DH ratchet)
# ============================================================

@dataclass
class RatchetState:
    root_key: bytes
    chain_key_send: bytes
    chain_key_recv: bytes


def _ratchet_step(chain_key: bytes, label: bytes) -> Tuple[bytes, bytes]:
    """
    label = b"A" or b"B"
    Returns:
        next_chain_key, message_key
    """
    msg_key = hkdf(chain_key, info=b"msg" + label, length=32)
    new_chain = hkdf(chain_key, info=b"ck" + label, length=32)
    return new_chain, msg_key


# ============================================================
#  Encryption
# ============================================================

def ratchet_encrypt(state: RatchetState, plaintext: str) -> dict:
    """
    - генеруємо msg_key з chain_key_send
    - AES-256-GCM
    - повертаємо nonce + ciphertext (base64)
    """

    new_chain, msg_key = _ratchet_step(state.chain_key_send, b"A")
    state.chain_key_send = new_chain

    aes = AESGCM(msg_key)
    nonce = os.urandom(12)
    ct = aes.encrypt(nonce, plaintext.encode("utf-8"), None)

    return {
        "nonce_b64": b64e(nonce),
        "ct_b64": b64e(ct),
    }


# ============================================================
#  Decryption
# ============================================================

def ratchet_decrypt(state: RatchetState, packet: dict) -> Tuple[str, RatchetState]:
    """
    - new_chain_recv, msg_key
    - aes.decrypt
    """

    new_chain, msg_key = _ratchet_step(state.chain_key_recv, b"B")
    state.chain_key_recv = new_chain

    aes = AESGCM(msg_key)
    nonce = b64d(packet["nonce_b64"])
    ct = b64d(packet["ct_b64"])

    plaintext = aes.decrypt(nonce, ct, None).decode("utf-8")
    return plaintext, state