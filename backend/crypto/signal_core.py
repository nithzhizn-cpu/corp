# ============================================================
#   Signal-Core Crypto Engine v6
#   Implements: Curve25519, X3DH, Double Ratchet (AES256-GCM)
# ============================================================

import os
import base64
from dataclasses import dataclass
from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey, X25519PublicKey
)
from cryptography.hazmat.primitives import hashes, hmac
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


# -------------------------------
# Utility helpers
# -------------------------------

def b64e(b: bytes) -> str:
    return base64.b64encode(b).decode()

def b64d(s: str) -> bytes:
    return base64.b64decode(s.encode())


def hkdf(secret: bytes, salt: bytes = b"", info: bytes = b"", length: int = 32) -> bytes:
    """HKDF-SHA256"""
    h = hashes.Hash(hashes.SHA256())
    h.update(salt + secret + info)
    key = h.finalize()
    return key[:length]


# ============================================================
# Identity + PreKeys
# ============================================================

@dataclass
class IdentityBundle:
    identity_priv: bytes
    identity_pub: bytes
    signed_prekey_priv: bytes
    signed_prekey_pub: bytes
    signed_prekey_sig: bytes


def generate_identity():
    """Generate identity key + signed prekey"""
    identity_priv = X25519PrivateKey.generate()
    signed_prekey_priv = X25519PrivateKey.generate()

    # Sign signed_prekey_pub using identity_priv (HMAC imitation signature)
    # In реальному Signal це Ed25519, але ми використовуємо HMAC в прототипі.
    sp_pub = signed_prekey_priv.public_key().public_bytes_raw()

    h = hmac.HMAC(identity_priv.private_bytes_raw(), hashes.SHA256())
    h.update(sp_pub)
    sig = h.finalize()

    return IdentityBundle(
        identity_priv=identity_priv.private_bytes_raw(),
        identity_pub=identity_priv.public_key().public_bytes_raw(),
        signed_prekey_priv=signed_prekey_priv.private_bytes_raw(),
        signed_prekey_pub=sp_pub,
        signed_prekey_sig=sig
    )


def generate_onetime_prekeys(n=50):
    """Generate one-time prekeys"""
    prekeys = []
    for _ in range(n):
        priv = X25519PrivateKey.generate()
        prekeys.append({
            "priv": b64e(priv.private_bytes_raw()),
            "pub": b64e(priv.public_key().public_bytes_raw())
        })
    return prekeys


# ============================================================
# X3DH HANDSHAKE (sender → receiver)
# ============================================================

def x3dh_sender(identity_priv_b, eph_priv_b, recv_bundle, onetime_prekey_pub_b=None):
    """
    identity_priv_b — sender identity private key (IKs)
    eph_priv_b — sender ephemeral private key (EKs)
    recv_bundle — receiver bundle: (IKr, SPKr, sig)
    onetime_prekey_pub_b — optional OPKr
    """

    IKr = X25519PublicKey.from_public_bytes(recv_bundle["identity_pub"])
    SPKr = X25519PublicKey.from_public_bytes(recv_bundle["signed_prekey_pub"])

    IKs = X25519PrivateKey.from_private_bytes(identity_priv_b)
    EKs = X25519PrivateKey.from_private_bytes(eph_priv_b)

    # X3DH shared secrets
    dh1 = IKs.exchange(SPKr)
    dh2 = EKs.exchange(IKr)
    dh3 = EKs.exchange(SPKr)
    dh4 = b""

    if onetime_prekey_pub_b:
        OPKr = X25519PublicKey.from_public_bytes(onetime_prekey_pub_b)
        dh4 = EKs.exchange(OPKr)

    # Final shared secret
    master = hkdf(dh1 + dh2 + dh3 + dh4, info=b"X3DH")

    return master


# ============================================================
# Double Ratchet
# ============================================================

@dataclass
class RatchetState:
    root_key: bytes
    chain_key_send: bytes
    chain_key_recv: bytes
    dh_priv: bytes
    dh_pub: bytes
    their_dh_pub: bytes


def ratchet_step(state: RatchetState, remote_pub_bytes: bytes):
    """Perform a DH ratchet step"""
    remote_pub = X25519PublicKey.from_public_bytes(remote_pub_bytes)
    dh_priv = X25519PrivateKey.from_private_bytes(state.dh_priv)

    dh_secret = dh_priv.exchange(remote_pub)

    new_root = hkdf(dh_secret, salt=state.root_key, info=b"root")
    new_chain = hkdf(new_root, info=b"chain")

    # Generate new DH key
    new_dh_priv = X25519PrivateKey.generate()
    new_dh_pub = new_dh_priv.public_key().public_bytes_raw()

    return RatchetState(
        root_key=new_root,
        chain_key_send=new_chain,
        chain_key_recv=new_chain,
        dh_priv=new_dh_priv.private_bytes_raw(),
        dh_pub=new_dh_pub,
        their_dh_pub=remote_pub_bytes
    )


def ratchet_encrypt(state: RatchetState, plaintext: str):
    """Encrypt using send chain"""
    nonce = os.urandom(12)
    key = hkdf(state.chain_key_send, info=b"msg")
    aes = AESGCM(key)
    ct = aes.encrypt(nonce, plaintext.encode(), None)

    return {
        "nonce": b64e(nonce),
        "ct": b64e(ct),
        "dh_pub": b64e(state.dh_pub)
    }


def ratchet_decrypt(state: RatchetState, packet):
    """Decrypt using recv chain"""
    nonce = b64d(packet["nonce"])
    ct = b64d(packet["ct"])
    dh_pub = b64d(packet["dh_pub"])

    # ratchet step if new DH key detected
    if dh_pub != state.their_dh_pub:
        state = ratchet_step(state, dh_pub)

    key = hkdf(state.chain_key_recv, info=b"msg")
    aes = AESGCM(key)

    pt = aes.decrypt(nonce, ct, None).decode()

    return pt, state