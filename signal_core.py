# ============================================================
#   Signal-Core Crypto Engine v6 (simplified)
#   Curve25519 + X3DH-style shared secret + symmetric ratchet
# ============================================================

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


def b64e(b: bytes) -> str:
    return base64.b64encode(b).decode("utf-8")


def b64d(s: str) -> bytes:
    return base64.b64decode(s.encode("utf-8"))


def hkdf(secret: bytes, salt: bytes = b"", info: bytes = b"", length: int = 32) -> bytes:
    """Проста HKDF-подібна функція на SHA256."""
    h = hashes.Hash(hashes.SHA256())
    h.update(salt)
    h.update(secret)
    h.update(info)
    k = h.finalize()
    return k[:length]


@dataclass
class IdentityBundle:
    identity_priv_b64: str
    identity_pub_b64: str
    signed_prekey_priv_b64: str
    signed_prekey_pub_b64: str
    signed_prekey_sig_b64: str


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


def generate_identity() -> IdentityBundle:
    """Генеруємо identity-key + signed-prekey (спрощено)."""
    identity_priv = X25519PrivateKey.generate()
    signed_prekey_priv = X25519PrivateKey.generate()

    identity_priv_bytes = _priv_bytes(identity_priv)
    identity_pub_bytes = _pub_bytes(identity_priv.public_key())

    spk_priv_bytes = _priv_bytes(signed_prekey_priv)
    spk_pub_bytes = _pub_bytes(signed_prekey_priv.public_key())

    # Псевдо-підпис (для демо): HKDF(identity_priv, spk_pub)
    sig = hkdf(spk_pub_bytes, salt=identity_priv_bytes, info=b"sig", length=32)

    return IdentityBundle(
        identity_priv_b64=b64e(identity_priv_bytes),
        identity_pub_b64=b64e(identity_pub_bytes),
        signed_prekey_priv_b64=b64e(spk_priv_bytes),
        signed_prekey_pub_b64=b64e(spk_pub_bytes),
        signed_prekey_sig_b64=b64e(sig),
    )


def generate_onetime_prekeys(n: int = 20):
    """Генеруємо n одноразових prekey."""
    prekeys = []
    for _ in range(n):
        priv = X25519PrivateKey.generate()
        priv_bytes = _priv_bytes(priv)
        pub_bytes = _pub_bytes(priv.public_key())
        prekeys.append({
            "priv_b64": b64e(priv_bytes),
            "pub_b64": b64e(pub_bytes),
        })
    return prekeys


def generate_ephemeral_key_b64() -> str:
    priv = X25519PrivateKey.generate()
    priv_bytes = _priv_bytes(priv)
    return b64e(priv_bytes)


def x3dh_sender(
    identity_priv_b64: str,
    eph_priv_b64: str,
    recv_bundle: dict,
    onetime_prekey_pub_b64: str | None = None,
) -> bytes:
    """Спрощена X3DH для демо."""
    IKr_pub = X25519PublicKey.from_public_bytes(
        b64d(recv_bundle["identity_pub_b64"])
    )
    SPKr_pub = X25519PublicKey.from_public_bytes(
        b64d(recv_bundle["signed_prekey_pub_b64"])
    )

    IKs_priv = X25519PrivateKey.from_private_bytes(b64d(identity_priv_b64))
    EKs_priv = X25519PrivateKey.from_private_bytes(b64d(eph_priv_b64))

    dh1 = IKs_priv.exchange(SPKr_pub)
    dh2 = EKs_priv.exchange(IKr_pub)
    dh3 = EKs_priv.exchange(SPKr_pub)
    dh4 = b""

    if onetime_prekey_pub_b64:
        OPKr_pub = X25519PublicKey.from_public_bytes(b64d(onetime_prekey_pub_b64))
        dh4 = EKs_priv.exchange(OPKr_pub)

    master = hkdf(dh1 + dh2 + dh3 + dh4, info=b"X3DH")
    return master


@dataclass
class RatchetState:
    root_key: bytes
    chain_key_send: bytes
    chain_key_recv: bytes


def _kdf_chain(chain_key: bytes, usage: bytes) -> tuple[bytes, bytes]:
    """KDF для оновлення chain key та отримання msg key."""
    msg_key = hkdf(chain_key, info=b"msg" + usage, length=32)
    new_chain = hkdf(chain_key, info=b"ck" + usage, length=32)
    return new_chain, msg_key


def ratchet_encrypt(state: RatchetState, plaintext: str) -> dict:
    """Шифрує повідомлення (оновлює chain_key_send)."""
    import os

    new_ck, msg_key = _kdf_chain(state.chain_key_send, usage=b"send")
    state.chain_key_send = new_ck

    aes = AESGCM(msg_key)
    nonce = os.urandom(12)
    ct = aes.encrypt(nonce, plaintext.encode("utf-8"), None)

    return {
        "nonce_b64": b64e(nonce),
        "ct_b64": b64e(ct),
    }


def ratchet_decrypt(state: RatchetState, packet: dict) -> tuple[str, RatchetState]:
    """Дешифрує повідомлення (оновлює chain_key_recv)."""
    new_ck, msg_key = _kdf_chain(state.chain_key_recv, usage=b"recv")
    state.chain_key_recv = new_ck

    aes = AESGCM(msg_key)
    nonce = b64d(packet["nonce_b64"])
    ct = b64d(packet["ct_b64"])
    pt = aes.decrypt(nonce, ct, None)
    return pt.decode("utf-8"), state
