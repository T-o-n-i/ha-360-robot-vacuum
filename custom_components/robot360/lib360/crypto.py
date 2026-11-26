"""Crypto helpers for the 360 account login and the push channel.

Uses only the ``cryptography`` package, which Home Assistant already ships.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from urllib.parse import quote

from cryptography.hazmat.primitives import padding as sym_padding
from cryptography.hazmat.primitives.asymmetric import padding as asym_padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.serialization import load_der_public_key

try:  # cryptography >= 43 moved DES/3DES to the "decrepit" module
    from cryptography.hazmat.decrepit.ciphers.algorithms import TripleDES
except ImportError:  # pragma: no cover - older cryptography
    from cryptography.hazmat.primitives.ciphers.algorithms import TripleDES  # type: ignore[attr-defined,no-redef]

# 1024-bit RSA public key of the 360 account service (X.509 DER, exponent 65537).
RSA_DER_HEX = (
    "30819f300d06092a864886f70d010101050003818d0030818902818100bda0d6470d7c86c4d35f"
    "0617e4ffe580b635444f5b0b590ada0c12c7774f36d4ec38ca9ea9fb3bc707ac9749412ddbf94b"
    "556ed0d3f4551eec67c2d83a70a61d0c89ea3339d22c82a35cf91de837dfb7c9f3f2f90e752525"
    "cd0b44dd3e1dbda6a06c7efa941181db0b8b34e5740f651c532bd1bb6a6e2ad623803366fced1a"
    "a50203010001"
)
_RSA_KEY = load_der_public_key(bytes.fromhex(RSA_DER_HEX))

_ALPHA = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"


def md5_hex(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()  # noqa: S324 - protocol requirement


def random_ascii(length: int) -> str:
    return "".join(_ALPHA[b % len(_ALPHA)] for b in secrets.token_bytes(length))


def random_hex(num_bytes: int) -> str:
    return secrets.token_hex(num_bytes)


def compute_sig(params: dict[str, str], secret: str = "") -> str:
    """md5 over all params except ``sig``, sorted by key, ``key=value`` without separator."""
    base = "".join(f"{key}={params[key]}" for key in sorted(params) if key != "sig")
    return md5_hex(base + secret)


def _encode_uri_component(value: str) -> str:
    # Same character set JavaScript's encodeURIComponent leaves untouched.
    return quote(value, safe="-_.!~*'()")


# --- DES-CBC (key = IV, PKCS7) -------------------------------------------------


def _des_cipher(key8: str) -> Cipher:
    raw = key8.encode("utf-8")
    if len(raw) != 8:
        raise ValueError("DES key must be 8 bytes")
    # 3DES with K1 = K2 = K3 is mathematically single DES.
    return Cipher(TripleDES(raw * 3), modes.CBC(raw))


def des_encrypt_b64(plain: str, key8: str) -> str:
    padder = sym_padding.PKCS7(64).padder()
    data = padder.update(plain.encode("utf-8")) + padder.finalize()
    enc = _des_cipher(key8).encryptor()
    return base64.b64encode(enc.update(data) + enc.finalize()).decode("ascii")


def des_decrypt_b64(b64: str, key8: str) -> str:
    dec = _des_cipher(key8).decryptor()
    data = dec.update(base64.b64decode(b64)) + dec.finalize()
    unpadder = sym_padding.PKCS7(64).unpadder()
    return (unpadder.update(data) + unpadder.finalize()).decode("utf-8")


# --- Login envelope ------------------------------------------------------------


def build_envelope(params: dict[str, str]) -> tuple[str, str, str]:
    """Return ``(parad, key, des_key)`` for a 360 account (QUC) request."""
    param_string = "&".join(f"{k}={_encode_uri_component(str(v))}" for k, v in params.items())
    key_string = random_ascii(106) + random_ascii(8)
    des_key = key_string[-8:]
    parad = des_encrypt_b64(param_string, des_key)
    key = base64.b64encode(
        _RSA_KEY.encrypt(key_string.encode("utf-8"), asym_padding.PKCS1v15())  # type: ignore[union-attr]
    ).decode("ascii")
    return parad, key, des_key


# --- Push payload (AES-128-CBC, key = IV = first 16 bytes of pushKey) -------------


def _aes_cipher(push_key: str) -> Cipher:
    key = push_key.encode("utf-8")[:16]
    if len(key) != 16:
        raise ValueError("pushKey too short")
    return Cipher(algorithms.AES(key), modes.CBC(key))


def aes_decrypt_b64(b64: str, push_key: str) -> str:
    dec = _aes_cipher(push_key).decryptor()
    data = dec.update(base64.b64decode(b64)) + dec.finalize()
    unpadder = sym_padding.PKCS7(128).unpadder()
    return (unpadder.update(data) + unpadder.finalize()).decode("utf-8")


def aes_encrypt_b64(plain: str, push_key: str) -> str:
    padder = sym_padding.PKCS7(128).padder()
    data = padder.update(plain.encode("utf-8")) + padder.finalize()
    enc = _aes_cipher(push_key).encryptor()
    return base64.b64encode(enc.update(data) + enc.finalize()).decode("ascii")
