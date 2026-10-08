import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from app.crypto import hybrid


@pytest.fixture(scope="module")
def key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def test_round_trip_large_payload(key):
    msg = b"x" * 10_000  # far beyond RSA's ~190-byte OAEP limit
    assert hybrid.decrypt(key, hybrid.encrypt(key.public_key(), msg)) == msg


def test_every_region_is_tamper_evident(key):
    blob = hybrid.encrypt(key.public_key(), b"pay bob 500")
    for i in (0, 255, 256, 260, 268, len(blob) - 1):  # wrapped key, nonce, ciphertext, tag
        bad = bytearray(blob)
        bad[i] ^= 1
        with pytest.raises(hybrid.CryptoError):
            hybrid.decrypt(key, bytes(bad))


def test_truncated_blob_rejected(key):
    with pytest.raises(hybrid.CryptoError):
        hybrid.decrypt(key, b"short")


def test_wrong_private_key_cannot_decrypt(key):
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(hybrid.CryptoError):
        hybrid.decrypt(other, hybrid.encrypt(key.public_key(), b"secret"))


def test_same_plaintext_gives_different_ciphertext(key):
    a, b = (hybrid.encrypt(key.public_key(), b"same") for _ in range(2))
    assert hybrid.ciphertext_hash(a) != hybrid.ciphertext_hash(b)
