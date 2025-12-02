# ============================================================
#   Signal-Core Crypto Engine v6.2
#   Curve25519 + X3DH + Double Ratchet (DH + Symmetric)
# ============================================================

import os
import base64
from dataclasses import dataclass

from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    PrivateFormat,
    PublicFormat,
    NoEncryption,
)


# ============================================================
#  Utilities
# ============================================================

def b64e(b: bytes) -> str:
    return base64.b64encode(b).decode()

def b64d(s: str) -> bytes:
    return base64.b64decode(s.encode())


def hkdf(secret: bytes, salt: bytes = b"", info: bytes = b"", length: int = 32) -> bytes:
    """HKDF (SHA-256)"""
    h = hashes.Hash(hashes.SHA256())
    h.update(salt)
    h.update(secret)
    h.update(info)
    return h.finalize()[:length]


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
#   Identity + PreKeys
# ============================================================

def generate_identity() -> dict:
    """Identity key pair + Signed PreKey, all in base64"""

    identity_priv = X25519PrivateKey.generate()
    spk_priv = X25519PrivateKey.generate()

    ik_priv = _priv_bytes(identity_priv)
    ik_pub = _pub_bytes(identity_priv.public_key())

    spk_priv_bytes = _priv_bytes(spk_priv)
    spk_pub_bytes = _pub_bytes(spk_priv.public_key())

    # fake signature (HKDF-based)
    sig = hkdf(spk_pub_bytes, salt=ik_priv, info=b"sig")

    return {
        "identity_priv_b64": b64e(ik_priv),
        "identity_pub_b64": b64e(ik_pub),
        "signed_prekey_priv_b64": b64e(spk_priv_bytes),
        "signed_prekey_pub_b64": b64e(spk_pub_bytes),
        "signed_prekey_sig_b64": b64e(sig),
    }


def generate_onetime_prekeys(n=20):
    """Generates one-time prekeys in base64"""
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


def generate_ephemeral_key_b64() -> str:
    """Generate ephemeral X25519 private key (base64)"""
    priv = X25519PrivateKey.generate()
    return b64e(_priv_bytes(priv))


def generate_ephemeral_keypair():
    """Return (pub_bytes, priv_bytes) as raw bytes"""
    priv = X25519PrivateKey.generate()
    priv_b = _priv_bytes(priv)
    pub_b = _pub_bytes(priv.public_key())
    return pub_b, priv_b


# ============================================================
#   X3DH sender-side
# ============================================================

def x3dh_sender(identity_priv_b64: str,
                eph_priv_b64: str,
                recv_bundle: dict,
                onetime_prekey_pub_b64: str | None = None) -> bytes:

    IKs_priv = X25519PrivateKey.from_private_bytes(b64d(identity_priv_b64))
    EKs_priv = X25519PrivateKey.from_private_bytes(b64d(eph_priv_b64))

    IKr_pub = X25519PublicKey.from_public_bytes(b64d(recv_bundle["identity_pub_b64"]))
    SPKr_pub = X25519PublicKey.from_public_bytes(b64d(recv_bundle["signed_prekey_pub_b64"]))

    # X3DH
    dh1 = IKs_priv.exchange(SPKr_pub)
    dh2 = EKs_priv.exchange(IKr_pub)
    dh3 = EKs_priv.exchange(SPKr_pub)
    dh4 = b""

    if onetime_prekey_pub_b64:
        OPKr_pub = X25519PublicKey.from_public_bytes(b64d(onetime_prekey_pub_b64))
        dh4 = EKs_priv.exchange(OPKr_pub)

    return hkdf(dh1 + dh2 + dh3 + dh4, info=b"X3DH")


# ============================================================
#  DOUBLE RATCHET (DH + Symmetric)
# ============================================================

@dataclass
class RatchetState:
    root_key: bytes
    dh_pub: bytes
    dh_priv: bytes
    chain_key_send: bytes
    chain_key_recv: bytes


def _dh(priv_b: bytes, pub_b: bytes) -> bytes:
    priv = X25519PrivateKey.from_private_bytes(priv_b)
    pub = X25519PublicKey.from_public_bytes(pub_b)
    return priv.exchange(pub)


def _kdf_root(root_key: bytes, dh_out: bytes) -> tuple[bytes, bytes]:
    """RK + DH → new_RK, CK"""
    new_root = hkdf(dh_out, salt=root_key, info=b"root", length=32)
    ck = hkdf(new_root, info=b"chain", length=32)
    return new_root, ck


def _kdf_chain(chain_key: bytes, direction: bytes) -> tuple[bytes, bytes]:
    """CK → new_CK, msg_key"""
    msg_key = hkdf(chain_key, info=b"msg" + direction, length=32)
    new_ck = hkdf(chain_key, info=b"ck" + direction, length=32)
    return new_ck, msg_key


def _dh_ratchet(state: RatchetState, remote_pub: bytes) -> RatchetState:
    """Perform DH ratchet step"""
    dh_out = _dh(state.dh_priv, remote_pub)

    new_root, new_ck = _kdf_root(state.root_key, dh_out)

    # generate new local DH
    new_dh_pub, new_dh_priv = generate_ephemeral_keypair()

    return RatchetState(
        root_key=new_root,
        dh_pub=new_dh_pub,
        dh_priv=new_dh_priv,
        chain_key_send=new_ck,
        chain_key_recv=new_ck,
    )


def ratchet_encrypt(state: RatchetState, plaintext: str) -> dict:
    """Encrypt with send chain"""
    new_ck, msg_key = _kdf_chain(state.chain_key_send, b"send")
    state.chain_key_send = new_ck

    aes = AESGCM(msg_key)
    nonce = os.urandom(12)
    ct = aes.encrypt(nonce, plaintext.encode(), None)

    return {
        "nonce_b64": b64e(nonce),
        "ct_b64": b64e(ct),
        "dh_pub_b64": b64e(state.dh_pub),
    }


def ratchet_decrypt(state: RatchetState, packet: dict) -> tuple[str, RatchetState]:
    """Decrypt with recv chain + DH-ratchet if needed"""

    remote_pub = b64d(packet["dh_pub_b64"])

    # If new DH key – ratchet
    if remote_pub != state.dh_pub:
        state = _dh_ratchet(state, remote_pub)

    new_ck, msg_key = _kdf_chain(state.chain_key_recv, b"recv")
    state.chain_key_recv = new_ck

    aes = AESGCM(msg_key)
    nonce = b64d(packet["nonce_b64"])
    ct = b64d(packet["ct_b64"])

    pt = aes.decrypt(nonce, ct, None).decode()
    return pt, state