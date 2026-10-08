from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey, RSAPublicKey


class ServerKeyHolder:
    """Holds the server's RSA-2048 keypair. In production the private key lives in an HSM / KMS."""

    def __init__(self, private_key: RSAPrivateKey):
        self._private = private_key

    @classmethod
    def load_or_generate(cls, pem_path: str | None) -> "ServerKeyHolder":
        if pem_path and Path(pem_path).exists():
            key = serialization.load_pem_private_key(Path(pem_path).read_bytes(), password=None)
            if not isinstance(key, RSAPrivateKey):
                raise TypeError("server key must be RSA")
            return cls(key)
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        if pem_path:
            Path(pem_path).write_bytes(
                key.private_bytes(
                    serialization.Encoding.PEM,
                    serialization.PrivateFormat.PKCS8,
                    serialization.NoEncryption(),
                )
            )
        return cls(key)

    @property
    def private_key(self) -> RSAPrivateKey:
        return self._private

    @property
    def public_key(self) -> RSAPublicKey:
        return self._private.public_key()

    def public_key_pem(self) -> str:
        return self.public_key.public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        ).decode()
