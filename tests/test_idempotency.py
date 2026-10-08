from concurrent.futures import ThreadPoolExecutor

from app.services.idempotency import InMemoryIdempotencyStore


def test_exactly_one_winner_among_many_threads():
    store = InMemoryIdempotencyStore(ttl_seconds=60)
    with ThreadPoolExecutor(64) as pool:
        wins = list(pool.map(lambda _: store.claim("h"), range(500)))
    assert wins.count(True) == 1


def test_claim_expires_after_ttl():
    now = [0.0]
    store = InMemoryIdempotencyStore(ttl_seconds=10, clock=lambda: now[0])
    assert store.claim("h") and not store.claim("h")
    now[0] = 11
    assert store.claim("h")


def test_release_and_eviction():
    now = [0.0]
    store = InMemoryIdempotencyStore(ttl_seconds=10, clock=lambda: now[0])
    store.claim("a"); store.claim("b")
    store.release("a")
    assert store.claim("a")
    now[0] = 99
    assert store.evict_expired() == 2 and len(store) == 0
