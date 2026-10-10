import time
from concurrent.futures import ThreadPoolExecutor

from tests.conftest import balance, flip_byte, make_packet, set_balance

from app.crypto import hybrid
from app.schemas import Outcome


def test_happy_path_settles_once(container):
    r = container.ingestion.ingest(make_packet(container), "b1", 2)
    assert r.outcome == Outcome.SETTLED and r.transaction_id
    assert balance(container, "abhishek@demo") == 27_00_000_00 - 500_00
    assert balance(container, "mridul@demo") == 22_00_000_00 + 500_00


def test_three_bridges_same_instant_settle_exactly_once(container):
    packet = make_packet(container)
    with ThreadPoolExecutor(3) as pool:
        results = list(pool.map(lambda n: container.ingestion.ingest(packet, f"b{n}"), range(3)))
    outcomes = sorted(r.outcome for r in results)
    assert outcomes == [Outcome.DUPLICATE_DROPPED, Outcome.DUPLICATE_DROPPED, Outcome.SETTLED]
    assert balance(container, "abhishek@demo") == 27_00_000_00 - 500_00  # debited ONCE


def test_stress_many_threads_one_packet(container):
    packet = make_packet(container)
    with ThreadPoolExecutor(32) as pool:
        results = list(pool.map(lambda n: container.ingestion.ingest(packet, f"b{n}"), range(100)))
    assert sum(r.outcome == Outcome.SETTLED for r in results) == 1


def test_db_unique_index_catches_duplicate_if_cache_fails(container):
    packet = make_packet(container)
    assert container.ingestion.ingest(packet).outcome == Outcome.SETTLED
    container.idempotency.clear()  # simulate Redis flush / restart
    assert container.ingestion.ingest(packet).outcome == Outcome.DUPLICATE_DROPPED
    assert balance(container, "abhishek@demo") == 27_00_000_00 - 500_00


def test_tampered_packet_is_invalid_and_moves_no_money(container):
    bad = flip_byte(make_packet(container), 300)
    assert container.ingestion.ingest(bad).outcome == Outcome.INVALID
    assert balance(container, "abhishek@demo") == 27_00_000_00


def test_garbage_base64_is_invalid(container):
    p = make_packet(container).model_copy(update={"ciphertext": "!!!not-base64!!!"})
    assert container.ingestion.ingest(p).outcome == Outcome.INVALID


def test_forged_packet_without_sender_key_is_rejected(container):
    """Anyone can encrypt to the server's PUBLIC key; only Alice's phone can sign for Alice."""
    from app.sender import SenderWallet
    mallory = SenderWallet.create("abhishek@demo", "0000")  # claims to be Alice, wrong device key
    p = mallory.create_packet(container.keys.public_key, "mallory@demo", 100_00, "0000", 5)
    r = container.ingestion.ingest(p)
    assert r.outcome == Outcome.INVALID and "signature" in r.reason
    assert balance(container, "abhishek@demo") == 27_00_000_00


def test_stale_packet_rejected(container):
    old = int((time.time() - 25 * 3600) * 1000)
    r = container.ingestion.ingest(make_packet(container, signed_at_ms=old))
    assert r.outcome == Outcome.INVALID and "old" in r.reason


def test_future_dated_packet_rejected(container):
    future = int((time.time() + 3600) * 1000)
    assert container.ingestion.ingest(make_packet(container, signed_at_ms=future)).outcome == Outcome.INVALID


def test_over_limit_amount_rejected(container):
    r = container.ingestion.ingest(make_packet(container, rupees=1_00_001))  # Re 1 over the Rs 1,00,000 cap
    assert r.outcome == Outcome.INVALID and "limit" in r.reason
    assert balance(container, "abhishek@demo") == 27_00_000_00


def test_amount_exactly_at_limit_settles(container):
    r = container.ingestion.ingest(make_packet(container, rupees=1_00_000))
    assert r.outcome == Outcome.SETTLED
    assert balance(container, "abhishek@demo") == 27_00_000_00 - 1_00_000_00


def test_insufficient_funds_rejected_and_audited(container):
    set_balance(container, "tanupriya@demo", 10_000_00)  # Rs 10,000, so two Rs 5,000 payments drain it
    send = lambda: make_packet(container, sender="tanupriya@demo", pin="2222", rupees=5_000)
    assert container.ingestion.ingest(send()).outcome == Outcome.SETTLED
    assert container.ingestion.ingest(send()).outcome == Outcome.SETTLED
    r = container.ingestion.ingest(send())
    assert r.outcome == Outcome.REJECTED and r.reason == "insufficient funds"
    assert balance(container, "tanupriya@demo") == 0


def test_same_instruction_reencrypted_is_nonce_replay(container):
    nonce = "a" * 32
    fixed = int(time.time() * 1000)
    p1 = make_packet(container, nonce=nonce, signed_at_ms=fixed)
    p2 = make_packet(container, nonce=nonce, signed_at_ms=fixed)  # different ciphertext, same payment
    assert container.ingestion.ingest(p1).outcome == Outcome.SETTLED
    assert container.ingestion.ingest(p2).outcome == Outcome.REJECTED
    assert balance(container, "abhishek@demo") == 27_00_000_00 - 500_00


def test_two_legitimate_identical_payments_both_settle(container):
    assert container.ingestion.ingest(make_packet(container)).outcome == Outcome.SETTLED
    assert container.ingestion.ingest(make_packet(container)).outcome == Outcome.SETTLED
    assert balance(container, "abhishek@demo") == 27_00_000_00 - 1000_00


def test_transient_failure_releases_claim(container, monkeypatch):
    packet = make_packet(container)
    def boom(*a, **k): raise RuntimeError("db down")
    monkeypatch.setattr(container.settlement, "settle", boom)
    try:
        container.ingestion.ingest(packet)
    except RuntimeError:
        pass
    monkeypatch.undo()
    assert container.ingestion.ingest(packet).outcome == Outcome.SETTLED  # retry works
