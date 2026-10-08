"""THE pipeline: hash -> claim -> decrypt -> validate -> freshness -> signature -> settle."""
import base64
import binascii
import logging
import time

from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.crypto import hybrid, signing
from app.models import Account
from app.schemas import IngestResult, MeshPacket, Outcome, SignedEnvelope
from app.services.idempotency import IdempotencyStore
from app.services.metrics import Metrics
from app.services.settlement import DuplicateSettlement, SettlementRejected, SettlementService

log = logging.getLogger("upimesh.ingest")


class BridgeIngestionService:
    def __init__(
        self,
        settings: Settings,
        private_key: RSAPrivateKey,
        idempotency: IdempotencyStore,
        settlement: SettlementService,
        session_factory: sessionmaker[Session],
        metrics: Metrics,
    ):
        self._s = settings
        self._key = private_key
        self._idem = idempotency
        self._settlement = settlement
        self._sf = session_factory
        self._metrics = metrics

    def ingest(
        self, packet: MeshPacket, bridge_node_id: str | None = None, hop_count: int | None = None
    ) -> IngestResult:
        result = self._ingest(packet, bridge_node_id, hop_count)
        self._metrics.incr(result.outcome.value)
        log.info("ingest outcome=%s hash=%s bridge=%s reason=%s",
                 result.outcome, (result.packet_hash or "-")[:12], bridge_node_id, result.reason)
        return result

    def _ingest(self, packet: MeshPacket, bridge: str | None, hops: int | None) -> IngestResult:
        # [1] hash the ciphertext
        try:
            blob = base64.b64decode(packet.ciphertext, validate=True)
        except (binascii.Error, ValueError):
            return IngestResult(outcome=Outcome.INVALID, reason="ciphertext is not valid base64")
        packet_hash = hybrid.ciphertext_hash(blob)

        # [2] atomic claim: duplicates die here, before any RSA work
        if not self._idem.claim(packet_hash):
            return IngestResult(outcome=Outcome.DUPLICATE_DROPPED, packet_hash=packet_hash)

        try:
            return self._process(blob, packet_hash, bridge, hops)
        except Exception:
            # Transient failure (DB down, ...): free the claim so a later delivery can retry.
            self._idem.release(packet_hash)
            raise

    def _process(self, blob: bytes, packet_hash: str, bridge: str | None, hops: int | None) -> IngestResult:
        def invalid(reason: str) -> IngestResult:
            return IngestResult(outcome=Outcome.INVALID, packet_hash=packet_hash, reason=reason)

        # [3] decrypt + authenticate (GCM tag) and parse
        try:
            envelope = SignedEnvelope.model_validate_json(hybrid.decrypt(self._key, blob))
        except hybrid.CryptoError:
            return invalid("decryption/authentication failed (tampered or wrong key)")
        except ValidationError:
            return invalid("malformed payload")
        instr = envelope.instruction

        # [4] freshness (replay window) and sanity limits
        now_ms = int(time.time() * 1000)
        if now_ms - instr.signed_at > self._s.max_packet_age_seconds * 1000:
            return invalid("packet too old")
        if instr.signed_at - now_ms > self._s.max_clock_skew_seconds * 1000:
            return invalid("signed_at is in the future")
        if instr.amount_paise > self._s.max_txn_paise:
            return invalid("amount exceeds offline transaction limit")

        # [5] sender's device signature
        with self._sf() as s:
            key = s.scalar(select(Account.device_public_key).where(Account.vpa == instr.sender))
        if key is None:
            return invalid("unknown sender")
        if not signing.verify(key, envelope.signature, instr):
            return invalid("bad sender signature")

        # [6] settle atomically
        try:
            tx = self._settlement.settle(instr, packet_hash, bridge, hops)
        except DuplicateSettlement:
            return IngestResult(outcome=Outcome.DUPLICATE_DROPPED, packet_hash=packet_hash)
        except SettlementRejected as exc:
            self._settlement.record_rejection(instr, packet_hash, str(exc), bridge)
            return IngestResult(outcome=Outcome.REJECTED, packet_hash=packet_hash, reason=str(exc))
        return IngestResult(outcome=Outcome.SETTLED, packet_hash=packet_hash, transaction_id=tx.id)
