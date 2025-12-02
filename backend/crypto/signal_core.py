# ============================================================
#   Signal-Core v7 (спрощений Signal-style engine)
#   X25519 identity + prekeys + X3DH-подібний master_secret
#   AES-256-GCM (один спільний ключ на пару користувачів)
#
#   ⚠ Це не повна реалізація Signal Double Ratchet.
#   Тут немає лічильників повідомлень та DH-ratchet.
#   Використовується один спільний ключ AES-GCM, отриманий
#   з X3DH/Curve25519, для шифрування/дешифрування.
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


# ------------------------------------------------------------
#   Base64 helpers
# ------------------------------------------------------------

def b64e(b: bytes) -> str:
    """bytes → base64 str"""
    return base64.b64encode(b).decode("utf-8")


def b64d(s: str) -> bytes:
    """base64 str → bytes"""
    return base64.b64decode(s.encode("utf-8"))


# ------------------------------------------------------------
#   HKDF-подібна функція (на SHA-256)
# ------------------------------------------------------------

def hkdf(secret: bytes, salt: bytes = b"", info: bytes = b"", length: int = 32) -> bytes:
    """
    Дуже проста HKDF-подібна функція:
    K = SHA256(salt || secret || info)[:length]
    Для нашого випадку цього достатньо.
    """
    h = hashes.Hash(hashes.SHA256())
    h.update(salt)
    h.update(secret)
    h.update(info)
    k = h.finalize()
    return k[:length]


# ============================================================
#   Identity + PreKeys (base64 формат для бекенду)
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


def generate_identity() -> dict:
    """
    Генерує identity key pair + signed prekey.
    Повертає dict з *_b64 полями (як очікує бекенд).

    Це аналог:
      - IK  (identity key)
      - SPK (signed prekey)
    Підпис SPK робимо псевдо-підписом через HKDF.
    """
    identity_priv = X25519PrivateKey.generate()
    signed_prekey_priv = X25519PrivateKey.generate()

    identity_priv_bytes = _priv_bytes(identity_priv)
    identity_pub_bytes = _pub_bytes(identity_priv.public_key())

    spk_priv_bytes = _priv_bytes(signed_prekey_priv)
    spk_pub_bytes = _pub_bytes(signed_prekey_priv.public_key())

    # Псевдо-підпис SPK: HKDF(spk_pub, salt=identity_priv)
    sig = hkdf(spk_pub_bytes, salt=identity_priv_bytes, info=b"sig", length=32)

    return {
        "identity_priv_b64": b64e(identity_priv_bytes),
        "identity_pub_b64": b64e(identity_pub_bytes),
        "signed_prekey_priv_b64": b64e(spk_priv_bytes),
        "signed_prekey_pub_b64": b64e(spk_pub_bytes),
        "signed_prekey_sig_b64": b64e(sig),
    }


def generate_onetime_prekeys(n: int = 20) -> list[dict]:
    """
    Генерує n одноразових prekey.
    Формат елемента:
    {
        "priv_b64": ...,
        "pub_b64": ...
    }
    """
    prekeys: list[dict] = []
    for _ in range(n):
        priv = X25519PrivateKey.generate()
        priv_bytes = _priv_bytes(priv)
        pub_bytes = _pub_bytes(priv.public_key())
        prekeys.append(
            {
                "priv_b64": b64e(priv_bytes),
                "pub_b64": b64e(pub_bytes),
            }
        )
    return prekeys


def generate_ephemeral_key_b64() -> str:
    """
    Генерує ефемерний (ephemeral) секретний ключ і повертає його в base64.

    У спрощеній схемі бекенд може це не використовувати,
    але функцію лишаємо для сумісності з попереднім кодом.
    """
    priv = X25519PrivateKey.generate()
    return b64e(_priv_bytes(priv))


# ============================================================
#   X3DH-подібний Handshake (sender → receiver)
#   (тут використовується тільки на бекенді для тестів або якщо ти захочеш)
# ============================================================

def x3dh_sender(
    identity_priv_b64: str,
    eph_priv_b64: str,
    recv_bundle: dict,
    onetime_prekey_pub_b64: str | None = None,
) -> bytes:
    """
    Спрощена X3DH-реалізація (одностороння).

    identity_priv_b64 — IK_s (identity priv sender)
    eph_priv_b64      — EK_s (ephemeral priv sender)
    recv_bundle       — {
        "identity_pub_b64": ...,
        "signed_prekey_pub_b64": ...,
        "signed_prekey_sig_b64": ...,
    }
    onetime_prekey_pub_b64 — опціональний one-time prekey (pub, base64)

    Повертає master_secret (bytes), який далі можна прогнати через HKDF.
    """

    IKr_pub = X25519PublicKey.from_public_bytes(
        b64d(recv_bundle["identity_pub_b64"])
    )
    SPKr_pub = X25519PublicKey.from_public_bytes(
        b64d(recv_bundle["signed_prekey_pub_b64"])
    )

    IKs_priv = X25519PrivateKey.from_private_bytes(b64d(identity_priv_b64))
    EKs_priv = X25519PrivateKey.from_private_bytes(b64d(eph_priv_b64))

    # X3DH компоненти (спрощено):
    dh1 = IKs_priv.exchange(SPKr_pub)
    dh2 = EKs_priv.exchange(IKr_pub)
    dh3 = EKs_priv.exchange(SPKr_pub)
    dh4 = b""

    if onetime_prekey_pub_b64:
        OPKr_pub = X25519PublicKey.from_public_bytes(b64d(onetime_prekey_pub_b64))
        dh4 = EKs_priv.exchange(OPKr_pub)

    master = hkdf(dh1 + dh2 + dh3 + dh4, info=b"X3DH", length=32)
    return master


# ============================================================
#   Спрощений "RatchetState" + AES-GCM
# ============================================================

@dataclass
class RatchetState:
    """
    Спрощений стан "ratchet" для бекенду.

    ⚠ На відміну від повного Signal Double Ratchet:
    - тут немає dh_ratchet, message numbers, skipped keys;
    - RatchetState тримає один root_key (shared_key),
      який використовується як основа для AES-ключа.

    Це простіше, стабільніше і не розʼїжджається,
    тому зникає cryptography.exceptions.InvalidTag через
    різні лічильники.
    """
    root_key: bytes


def _derive_msg_key(root_key: bytes) -> bytes:
    """
    Отримуємо 32-байтний AES-ключ з root_key.
    Для простоти info = b"msg_key_v1".
    """
    return hkdf(root_key, info=b"msg_key_v1", length=32)


def ratchet_encrypt(state: RatchetState, plaintext: str) -> dict:
    """
    Шифрує повідомлення, використовуючи AES-256-GCM з ключа,
    отриманого з state.root_key.

    Повертає:
    {
        "nonce_b64": ...,
        "ct_b64": ...
    }

    Бекенд може зберігати цей пакет як є, не торкаючись plaintext.
    """
    key = _derive_msg_key(state.root_key)
    aes = AESGCM(key)

    nonce = os.urandom(12)
    ct = aes.encrypt(nonce, plaintext.encode("utf-8"), None)

    return {
        "nonce_b64": b64e(nonce),
        "ct_b64": b64e(ct),
    }


def ratchet_decrypt(state: RatchetState, packet: dict) -> tuple[str, RatchetState]:
    """
    Дешифрує пакет, зашифрований ratchet_encrypt.

    Якщо ключ не підходить (невірна сесія/маніпуляція),
    AESGCM підніме InvalidTag.

    Повертає (plaintext, state).
    Тут state не змінюється, але повертаємо його для
    сумісності з попереднім API.
    """
    key = _derive_msg_key(state.root_key)
    aes = AESGCM(key)

    nonce = b64d(packet["nonce_b64"])
    ct = b64d(packet["ct_b64"])

    plaintext = aes.decrypt(nonce, ct, None).decode("utf-8")
    return plaintext, state