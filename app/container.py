"""Manual dependency wiring (no framework magic, easy to swap pieces in tests)."""
from dataclasses import dataclass

from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.crypto.keys import ServerKeyHolder
from app.db import Base, make_engine, make_session_factory
from app.services.demo import DemoService
from app.services.idempotency import InMemoryIdempotencyStore, RedisIdempotencyStore
from app.services.ingestion import BridgeIngestionService
from app.services.mesh import MeshSimulator
from app.services.metrics import Metrics
from app.services.ratelimit import TokenBucketLimiter
from app.services.settlement import SettlementService


@dataclass
class Container:
    settings: Settings
    engine: Engine
    session_factory: sessionmaker[Session]
    keys: ServerKeyHolder
    idempotency: object
    settlement: SettlementService
    ingestion: BridgeIngestionService
    mesh: MeshSimulator
    demo: DemoService
    metrics: Metrics
    limiter: TokenBucketLimiter


def build_container(settings: Settings) -> Container:
    engine = make_engine(settings.database_url)
    Base.metadata.create_all(engine)
    sf = make_session_factory(engine)
    keys = ServerKeyHolder.load_or_generate(settings.private_key_path)
    idem = (
        RedisIdempotencyStore(settings.redis_url, settings.idempotency_ttl_seconds)
        if settings.redis_url
        else InMemoryIdempotencyStore(settings.idempotency_ttl_seconds)
    )
    metrics = Metrics()
    settlement = SettlementService(sf)
    ingestion = BridgeIngestionService(settings, keys.private_key, idem, settlement, sf, metrics)
    demo = DemoService(sf, keys, settings.packet_ttl)
    if settings.seed_demo_data:
        demo.seed()
    return Container(
        settings, engine, sf, keys, idem, settlement, ingestion,
        MeshSimulator(ingestion, settings.packet_ttl), demo, metrics,
        TokenBucketLimiter(settings.bridge_rate_limit_per_minute),
    )
