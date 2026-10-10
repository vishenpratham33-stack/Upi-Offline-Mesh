def _send(client, **over):
    body = {"sender": "abhishek@demo", "receiver": "mridul@demo", "amount": "500.00", "pin": "1234"}
    return client.post("/api/demo/send", json={**body, **over})


def _balances(client):
    return {a["vpa"]: a["balance_paise"] for a in client.get("/api/accounts").json()}


def test_full_demo_flow_with_two_bridges(client):
    assert _send(client).status_code == 200
    assert client.post("/api/mesh/gossip").json()["transfers"] == 4  # the sender's phone -> the 4 other phones
    assert client.post("/api/mesh/gossip").json()["transfers"] == 0  # already everywhere
    state = client.get("/api/mesh/state").json()
    assert all(d["packet_count"] == 1 for d in state)  # everyone holds it

    results = client.post("/api/mesh/flush").json()["results"]
    outcomes = sorted(r["outcome"] for r in results)
    assert outcomes == ["DUPLICATE_DROPPED", "SETTLED"]  # 2 bridges, settled once

    b = _balances(client)
    assert b["abhishek@demo"] == 27_00_000_00 - 500_00 and b["mridul@demo"] == 22_00_000_00 + 500_00
    assert client.get("/api/transactions").json()[0]["status"] == "SETTLED"
    assert client.get("/api/metrics").json()["outcomes"]["SETTLED"] == 1


def test_ttl_limits_propagation(client):
    _send(client)
    for _ in range(10):
        client.post("/api/mesh/gossip")
    ttls = {p["ttl"] for d in client.get("/api/mesh/state").json() for p in d["packets"]}
    assert min(ttls) >= 0  # never negative


def test_wrong_pin_rejected(client):
    assert _send(client, pin="9999").status_code == 403


def test_unknown_sender_and_bad_amount(client):
    assert _send(client, sender="nobody@demo").status_code == 404
    assert _send(client, amount="1.234").status_code == 422
    assert _send(client, amount="-5").status_code == 422


def test_bridge_ingest_endpoint_and_duplicate(client):
    _send(client)
    pkt = client.get("/api/mesh/state").json()[0]["packets"][0]
    # take the real packet back out through flush of an offline device isn't possible, so build one:
    c = client.app.state.container
    from tests.conftest import make_packet
    p = make_packet(c)
    hdr = {"X-Bridge-Node-Id": "phone-bridge-42", "X-Hop-Count": "3"}
    r1 = client.post("/api/bridge/ingest", json=p.model_dump(), headers=hdr)
    r2 = client.post("/api/bridge/ingest", json=p.model_dump(), headers=hdr)
    assert r1.json()["outcome"] == "SETTLED" and r1.json()["transaction_id"]
    assert r2.json()["outcome"] == "DUPLICATE_DROPPED"
    assert pkt["packet_id"]


def test_ingest_validates_body(client):
    r = client.post("/api/bridge/ingest", json={"packet_id": "x", "ttl": -1})
    assert r.status_code == 422


def test_rate_limit_returns_429(settings):
    from fastapi.testclient import TestClient
    from app.main import create_app
    s = settings.model_copy(update={"bridge_rate_limit_per_minute": 2})
    with TestClient(create_app(s)) as c:
        c_ = c.app.state.container
        from tests.conftest import make_packet
        codes = [c.post("/api/bridge/ingest", json=make_packet(c_).model_dump(),
                        headers={"X-Bridge-Node-Id": "spammer"}).status_code for _ in range(4)]
    assert codes[:2] == [200, 200] and 429 in codes[2:]


def test_reset_clears_mesh_and_cache(client):
    _send(client); client.post("/api/mesh/reset")
    assert all(d["packet_count"] == 0 for d in client.get("/api/mesh/state").json())
    assert client.get("/api/metrics").json()["idempotency_entries"] == 0


def test_server_key_and_dashboard(client):
    assert "BEGIN PUBLIC KEY" in client.get("/api/server-key").json()["public_key_pem"]
    assert client.get("/api/health").json() == {"status": "ok"}


def _pay(client, **over):
    body = {"sender": "abhishek@demo", "receiver": "mridul@demo", "amount": "500.00", "pin": "1234"}
    return client.post("/api/pay", json={**body, **over})


def test_pay_runs_whole_flow_in_one_call(client):
    r = _pay(client)
    assert r.status_code == 200
    t = r.json()
    assert t["result"] == "SETTLED" and t["origin"] == "phone-sender"
    assert len(t["rounds"][0]) == 4                       # the sender's phone -> the other 4 phones
    assert sorted(u["outcome"] for u in t["uploads"]) == ["DUPLICATE_DROPPED", "SETTLED"]
    assert {d["device_id"] for d in t["devices"] if d["has_internet"]} == {"phone-bridge", "phone-bridge-2"}
    b = _balances(client)
    assert b["abhishek@demo"] == 27_00_000_00 - 500_00 and b["mridul@demo"] == 22_00_000_00 + 500_00


def test_pay_twice_settles_twice(client):
    assert _pay(client).json()["result"] == "SETTLED"
    assert _pay(client).json()["result"] == "SETTLED"
    assert _balances(client)["abhishek@demo"] == 27_00_000_00 - 1000_00


def test_pay_reports_rejection_with_reason(client):
    from tests.conftest import set_balance
    set_balance(client.app.state.container, "tanupriya@demo", 10_000_00)  # Rs 10,000
    for _ in range(2):
        assert _pay(client, sender="tanupriya@demo", pin="2222", amount="5000").json()["result"] == "SETTLED"
    t = _pay(client, sender="tanupriya@demo", pin="2222", amount="5000").json()
    assert t["result"] == "REJECTED" and t["reason"] == "insufficient funds"


def test_pay_error_codes(client):
    assert _pay(client, pin="9999").status_code == 403
    assert _pay(client, sender="nobody@demo").status_code == 404
    assert _pay(client, amount="1.234").status_code == 422


def test_pay_concurrent_visitors_do_not_corrupt_each_other(client):
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(4) as pool:
        results = list(pool.map(lambda _: _pay(client).json()["result"], range(4)))
    assert results == ["SETTLED"] * 4
    assert _balances(client)["abhishek@demo"] == 27_00_000_00 - 4 * 500_00


def test_demo_accounts_names_and_balances(client):
    got = {a["name"]: a["balance_paise"] for a in client.get("/api/accounts").json()}
    assert got == {"Abhishek": 27_00_000_00, "Mridul": 22_00_000_00,
                   "Mika": 50_00_000_00, "Tanupriya": 35_00_000_00}


def test_pay_one_lakh_works_and_over_one_lakh_is_refused(client):
    ok = _pay(client, amount="100000").json()
    assert ok["result"] == "SETTLED"
    over = _pay(client, amount="100000.01").json()
    assert over["result"] == "INVALID" and "limit" in over["reason"]


def test_legacy_demo_accounts_are_removed_on_start(settings):
    from app.container import build_container
    from app.models import Account
    c1 = build_container(settings)
    with c1.session_factory() as s, s.begin():
        s.add(Account(vpa="alice@demo", name="Alice", balance_paise=1, device_public_key="x"))
    c2 = build_container(settings)  # same DB file, simulates restart with an old database
    with c2.session_factory() as s:
        vpas = {a.vpa for a in s.query(Account)}
    assert "alice@demo" not in vpas and "abhishek@demo" in vpas
