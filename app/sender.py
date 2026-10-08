"""Device-side logic: what the sender's phone does while offline.

Kept free of server dependencies so it could be ported to an Android/Kotlin client.
"""
import base64
import hashlib
import hmac
import os
import time
import uuid
from dataclasses import dataclass, field

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey

from app.crypto import hybrid, signing
from app.schemas import MeshPacket, PaymentInstruction, SignedEnvelope


class WrongPin(Exception):
    pass


def _scrypt(pin: str, salt: bytes) -> bytes:
    return hashlib.scrypt(pin.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)


@dataclass
class SenderWallet:
    """A phone's wallet. The PIN unlocks the device signing key *locally*, so the PIN never
    travels over the mesh (the original design shipped a PIN hash inside the payload)."""

    vpa: str
    signing_key: Ed25519PrivateKey
    _salt: bytes = field(default_factory=lambda: os.urandom(16))
    _pin_digest: bytes = b""

    @classmethod
    def create(cls, vpa: str, pin: str) -> "SenderWallet":
        w = cls(vpa=vpa, signing_key=Ed25519PrivateKey.generate())
        w._pin_digest = _scrypt(pin, w._salt)
        return w

    @property
    def public_key_b64(self) -> str:
        return signing.public_key_b64(self.signing_key)

    def create_packet(
        self,
        server_public_key: RSAPublicKey,
        receiver: str,
        amount_paise: int,
        pin: str,
        ttl: int,
        *,
        signed_at_ms: int | None = None,
        nonce: str | None = None,
    ) -> MeshPacket:
        if not hmac.compare_digest(_scrypt(pin, self._salt), self._pin_digest):
            raise WrongPin("incorrect PIN")
        now = int(time.time() * 1000)
        instruction = PaymentInstruction(
            sender=self.vpa,
            receiver=receiver,
            amount_paise=amount_paise,
            nonce=nonce or uuid.uuid4().hex,
            signed_at=signed_at_ms or now,
        )
        envelope = SignedEnvelope(
            instruction=instruction, signature=signing.sign(self.signing_key, instruction)
        )
        blob = hybrid.encrypt(server_public_key, envelope.model_dump_json().encode())
        return MeshPacket(
            packet_id=str(uuid.uuid4()),
            ttl=ttl,
            created_at=now,
            ciphertext=base64.b64encode(blob).decode(),
        )
