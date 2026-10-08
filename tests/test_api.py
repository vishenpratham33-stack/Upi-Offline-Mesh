def _send(client, **over):
    body = {"sender": "alice@demo", "receiver": "bob@demo", "amount": "500.00", "pin": "1234"}
    return client.post("/api/demo/send", json={**body, **over})


def _balances(client):
    return {a["vpa"]: a["balance_paise"] for a in client.get("/api/accounts").json()}


def test_full_demo_flow_with_two_bridges(client):
    assert _send(client).status_code == 200
    assert client.post("/api/mesh/gossip").json()["transfers"] == 4  # alice -> 4 other phones
    assert client.post("/api/mesh/gossip").json()["transfers"] == 0  # already everywhere
    state = client.get("/api/mesh/state").json()
    assert all(d["packet_count"] == 1 for d in state)  # everyone holds it

    results = client.post("/api/mesh/flush").json()["results"]
    outcomes = sorted(r["outcome"] for r in results)
    assert outcomes == ["DUPLICATE_DROPPED", "SETTLED"]  # 2 bridges, settled once

    b = _balances(client)
    assert b["alice@demo"] == 100_000_00 - 500_00 and b["bob@demo"] == 50_000_00 + 500_00
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
