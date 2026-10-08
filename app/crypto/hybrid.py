"""Hybrid encryption: RSA-OAEP(SHA-256) wraps a fresh AES-256 key, AES-GCM encrypts the payload.

Wire layout:  [ RSA-wrapped AES key (key_size/8 bytes) | 12-byte nonce | AES-GCM ciphertext + 16-byte tag ]

GCM is authenticated: flipping any bit anywhere makes decryption raise, so a malicious
intermediate phone can neither read nor modify a packet.
"""
import hashlib
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey, RSAPublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

NONCE_LEN = 12
TAG_LEN = 16
AAD = b"upi-mesh/v1"  # binds ciphertexts to this protocol version

_OAEP = padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None)


class CryptoError(Exception):
    """Raised for any decryption / authentication failure (deliberately uninformative)."""


def encrypt(public_key: RSAPublicKey, plaintext: bytes) -> bytes:
    aes_key = AESGCM.generate_key(bit_length=256)
    nonce = os.urandom(NONCE_LEN)
    body = AESGCM(aes_key).encrypt(nonce, plaintext, AAD)
    return public_key.encrypt(aes_key, _OAEP) + nonce + body


def decrypt(private_key: RSAPrivateKey, blob: bytes) -> bytes:
    wrapped_len = private_key.key_size // 8
    if len(blob) < wrapped_len + NONCE_LEN + TAG_LEN:
        raise CryptoError("ciphertext too short")
    wrapped, nonce = blob[:wrapped_len], blob[wrapped_len : wrapped_len + NONCE_LEN]
    body = blob[wrapped_len + NONCE_LEN :]
    try:
        aes_key = private_key.decrypt(wrapped, _OAEP)
        return AESGCM(aes_key).decrypt(nonce, body, AAD)
    except (ValueError, InvalidTag) as exc:
        raise CryptoError("decryption failed") from exc


def ciphertext_hash(blob: bytes) -> str:
    """Idempotency key. Hashing ciphertext (not packet_id) lets us dedupe before any RSA work
    and can't be dodged by a relay rewriting the unauthenticated packet_id."""
    return hashlib.sha256(blob).hexdigest()
