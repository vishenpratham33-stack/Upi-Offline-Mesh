"""ORM entities."""
from datetime import datetime, timezone

from sqlalchemy import BigInteger, DateTime, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    vpa: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(100))
    balance_paise: Mapped[int] = mapped_column(BigInteger)
    # Base64 Ed25519 public key of the account holder's phone (registered when online).
    device_public_key: Mapped[str] = mapped_column(String(64))
    # Optimistic locking: SQLAlchemy adds "WHERE version = ?" to every UPDATE.
    version: Mapped[int] = mapped_column(Integer, default=1)

    __mapper_args__ = {"version_id_col": version}


class Transaction(Base):
    __tablename__ = "transactions"
    __table_args__ = (UniqueConstraint("sender_vpa", "nonce", name="uq_sender_nonce"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    # Last line of defence if the idempotency cache ever fails.
    packet_hash: Mapped[str] = mapped_column(String(64), unique=True)
    sender_vpa: Mapped[str] = mapped_column(String(64))
    receiver_vpa: Mapped[str] = mapped_column(String(64))
    amount_paise: Mapped[int] = mapped_column(BigInteger)
    nonce: Mapped[str] = mapped_column(String(64))
    signed_at: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(16))  # SETTLED | REJECTED
    reason: Mapped[str | None] = mapped_column(String(200), default=None)
    bridge_node_id: Mapped[str | None] = mapped_column(String(64), default=None)
    hop_count: Mapped[int | None] = mapped_column(Integer, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
