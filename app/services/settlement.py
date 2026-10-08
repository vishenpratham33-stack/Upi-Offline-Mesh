"""Debit / credit / ledger-write in ONE database transaction."""
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.orm.exc import StaleDataError

from app.models import Account, Transaction
from app.schemas import PaymentInstruction


class SettlementRejected(Exception):
    """Business-rule failure (unknown account, insufficient funds, ...). Nothing was moved."""


class DuplicateSettlement(Exception):
    """The unique index on packet_hash fired: this packet already settled."""


class SettlementService:
    MAX_RETRIES = 5

    def __init__(self, session_factory: sessionmaker[Session]):
        self._sf = session_factory

    def settle(
        self,
        instr: PaymentInstruction,
        packet_hash: str,
        bridge_node_id: str | None = None,
        hop_count: int | None = None,
    ) -> Transaction:
        if instr.sender == instr.receiver:
            raise SettlementRejected("sender and receiver are the same account")
        for _ in range(self.MAX_RETRIES):
            try:
                with self._sf() as s, s.begin():
                    accounts = {
                        a.vpa: a
                        for a in s.scalars(
                            select(Account).where(Account.vpa.in_([instr.sender, instr.receiver]))
                        )
                    }
                    sender, receiver = accounts.get(instr.sender), accounts.get(instr.receiver)
                    if sender is None or receiver is None:
                        raise SettlementRejected("unknown sender or receiver")
                    if sender.balance_paise < instr.amount_paise:
                        raise SettlementRejected("insufficient funds")
                    sender.balance_paise -= instr.amount_paise
                    receiver.balance_paise += instr.amount_paise
                    tx = Transaction(
                        packet_hash=packet_hash,
                        sender_vpa=instr.sender,
                        receiver_vpa=instr.receiver,
                        amount_paise=instr.amount_paise,
                        nonce=instr.nonce,
                        signed_at=instr.signed_at,
                        status="SETTLED",
                        bridge_node_id=bridge_node_id,
                        hop_count=hop_count,
                    )
                    s.add(tx)
                    s.flush()
                return tx
            except StaleDataError:
                continue  # another settlement touched an account: retry on fresh data
            except IntegrityError as exc:
                if self._hash_exists(packet_hash):
                    raise DuplicateSettlement from exc
                raise SettlementRejected("replayed nonce for this sender") from exc
        raise SettlementRejected("could not settle due to contention")

    def record_rejection(
        self, instr: PaymentInstruction, packet_hash: str, reason: str, bridge: str | None
    ) -> None:
        """Audit trail for rejected packets. Best-effort: a unique clash means it's already logged."""
        try:
            with self._sf() as s, s.begin():
                s.add(
                    Transaction(
                        packet_hash=packet_hash,
                        sender_vpa=instr.sender,
                        receiver_vpa=instr.receiver,
                        amount_paise=instr.amount_paise,
                        nonce=instr.nonce,
                        signed_at=instr.signed_at,
                        status="REJECTED",
                        reason=reason[:200],
                        bridge_node_id=bridge,
                    )
                )
        except IntegrityError:
            pass

    def _hash_exists(self, packet_hash: str) -> bool:
        with self._sf() as s:
            return s.scalar(select(Transaction.id).where(Transaction.packet_hash == packet_hash)) is not None
