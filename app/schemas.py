"""Wire formats (pydantic v2 does all validation/serialisation)."""
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class MeshPacket(BaseModel):
    """What travels between phones. Only `ciphertext` is secret; the rest is routing metadata."""

    packet_id: str = Field(min_length=8, max_length=64)
    ttl: int = Field(ge=0, le=32)
    created_at: int  # epoch millis
    ciphertext: str = Field(min_length=1, max_length=65_536)  # base64


class PaymentInstruction(BaseModel):
    model_config = ConfigDict(frozen=True)

    sender: str
    receiver: str
    amount_paise: int = Field(gt=0)
    nonce: str = Field(min_length=16, max_length=64)
    signed_at: int  # epoch millis


class SignedEnvelope(BaseModel):
    """Plaintext that is hybrid-encrypted: the instruction plus the sender's signature over it."""

    instruction: PaymentInstruction
    signature: str  # base64 Ed25519


class Outcome(StrEnum):
    SETTLED = "SETTLED"
    DUPLICATE_DROPPED = "DUPLICATE_DROPPED"
    INVALID = "INVALID"
    REJECTED = "REJECTED"


class IngestResult(BaseModel):
    outcome: Outcome
    packet_hash: str | None = None
    reason: str | None = None
    transaction_id: int | None = None


class SendRequest(BaseModel):
    sender: str
    receiver: str
    amount: Decimal = Field(gt=0, max_digits=10, decimal_places=2, description="Rupees")
    pin: str = Field(min_length=4, max_length=8)
    device_id: str = "phone-sender"
