"""Ed25519 device signatures.

The server's RSA public key is, by definition, public, so encryption alone proves nothing about
WHO created a packet. Each phone therefore signs its instruction with a device key registered
while online; the server verifies the signature against the sender's stored key.
"""
import base64
import json

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from app.schemas import PaymentInstruction


def canonical_bytes(instruction: PaymentInstruction) -> bytes:
    """Deterministic serialisation so signer and verifier hash identical bytes."""
    return json.dumps(
        instruction.model_dump(), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()


def public_key_b64(key: Ed25519PrivateKey) -> str:
    raw = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return base64.b64encode(raw).decode()


def sign(key: Ed25519PrivateKey, instruction: PaymentInstruction) -> str:
    return base64.b64encode(key.sign(canonical_bytes(instruction))).decode()


def verify(public_b64: str, signature_b64: str, instruction: PaymentInstruction) -> bool:
    try:
        pub = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_b64))
        pub.verify(base64.b64decode(signature_b64), canonical_bytes(instruction))
        return True
    except (InvalidSignature, ValueError):
        return False
