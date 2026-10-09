# UPI Offline Mesh (Python)
   ![Dashboard](docs/dashboard.png)

   **Live demo:** https://upi-offline-mesh-80hu.onrender.com

Offline UPI-style payments routed through a phone-to-phone mesh, settled exactly once by a FastAPI backend.

You're in a basement with no signal. Your phone signs and encrypts a payment, hands it to nearby phones, and the packet hops device to device until one of them reaches 4G and uploads it. The server decrypts, verifies, deduplicates and settles. A software simulator of the mesh lets you demo the whole flow on one laptop.

> This is an independent Python re-implementation with a different stack and extra security features.

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
uvicorn --factory app.main:get_app --reload
```

Open <http://localhost:8000> for the dashboard and <http://localhost:8000/docs> for the interactive API docs.
Run the tests with `pytest -q`. Or use Docker: `docker build -t upimesh . && docker run -p 8000:8000 upimesh`.

## Demo flow

1. **Send into the mesh.** The simulated sender phone checks your PIN locally, signs the instruction, encrypts it, and hands it to `phone-alice`.
2. **Run a gossip round.** Every phone shares what it holds with every phone in range. TTL drops per hop.
3. **Bridges upload.** Two phones have 4G. Both upload the same packet in parallel. One settles, the other is dropped as a duplicate.

## Architecture

```
SENDER PHONE (offline)
  PaymentInstruction{sender, receiver, amount_paise, nonce, signed_at}
  + Ed25519 signature (device key, unlocked by PIN)
  -> hybrid encrypt with server RSA public key -> MeshPacket{packet_id, ttl, created_at, ciphertext}
        |  gossip over BLE / Wi-Fi Direct
        v
  stranger -> stranger -> BRIDGE (gets 4G) -- HTTPS POST /api/bridge/ingest -->
        v
SERVER  1. SHA-256(ciphertext)
        2. IdempotencyStore.claim(hash)      atomic; duplicates die here, before any RSA work
        3. RSA-OAEP unwrap + AES-256-GCM decrypt (tag verifies integrity)
        4. freshness window, future-dating check, per-txn limit
        5. verify sender's Ed25519 signature against the registered device key
        6. settle: one DB transaction, optimistic locking, UNIQUE(packet_hash), UNIQUE(sender, nonce)
```

## What's different from the original

| Area | Original (Java) | This project (Python) |
|---|---|---|
| Sender authenticity | Anyone with the public server key can forge a payment | **Ed25519 device signatures** verified per packet |
| PIN | PIN hash travels inside the packet | PIN unlocks the signing key **locally** (scrypt); never on the wire |
| Ciphertext binding | none | AES-GCM **AAD** binds to protocol version |
| Idempotency | `ConcurrentHashMap` only | Pluggable store: in-memory (TTL, lock) or **Redis `SET NX EX`** |
| Failure handling | n/a | Claim is **released** on transient errors so retries work |
| Replay | timestamp + nonce | + `UNIQUE(sender, nonce)` + **future-dating** check |
| Money | decimals | **Integer paise** (no float error) |
| Abuse | none | **Per-bridge token-bucket rate limiting**, per-txn limit |
| Audit | settled only | **Rejected packets are logged** with reasons |
| Gossip dedupe | by `packetId` (rewritable by relays) | by **ciphertext hash** |
| Demo | 1 bridge (edit code to see duplicates) | **2 bridges** by default, duplicate-storm visible out of the box |
| Ops | none | `/api/metrics`, structured logs, Docker, GitHub Actions CI |

## Libraries instead of hand-rolled code

| Concern | Library |
|---|---|
| Web API, docs, validation | FastAPI + pydantic v2 |
| Crypto (RSA-OAEP, AES-GCM, Ed25519) | `cryptography` |
| ORM, optimistic locking | SQLAlchemy 2 (`version_id_col`) |
| Config | pydantic-settings (`UPIMESH_*` env vars) |
| Password-style KDF for PIN | `hashlib.scrypt` (stdlib) |

## API

| Method | Path | Purpose |
|---|---|---|
| GET | `/` | Dashboard |
| GET | `/api/server-key` | Server RSA public key (PEM) |
| GET | `/api/accounts` , `/api/transactions` | Balances / ledger |
| GET | `/api/mesh/state` , `/api/metrics` | Mesh state / outcome counters |
| POST | `/api/demo/send` | Simulate a sender phone: sign, encrypt, inject |
| POST | `/api/mesh/gossip` , `/flush` , `/reset` | Run a hop / bridges upload / reset |
| POST | `/api/bridge/ingest` | **Production endpoint** (headers `X-Bridge-Node-Id`, `X-Hop-Count`) |

Ingest response: `{"outcome": "SETTLED|DUPLICATE_DROPPED|INVALID|REJECTED", "packet_hash": "...", "reason": null, "transaction_id": 42}`

## Tests (31)

- Crypto: round trip beyond RSA's size limit, tamper detection in every region, wrong key.
- Idempotency: 500 racing threads yield exactly one winner; TTL expiry; eviction.
- Pipeline: **3 bridges at once settle once**; 100-thread stress; DB unique index catches duplicates when the cache is wiped; forged signature; stale and future packets; over-limit; insufficient funds; nonce replay; retry after transient failure.
- API: end-to-end demo flow, validation errors, wrong PIN, rate limiting (429).

## Configuration

`UPIMESH_DATABASE_URL`, `UPIMESH_REDIS_URL`, `UPIMESH_PRIVATE_KEY_PATH`, `UPIMESH_PACKET_TTL`, `UPIMESH_MAX_TXN_PAISE`, `UPIMESH_BRIDGE_RATE_LIMIT_PER_MINUTE`, and more in `app/config.py`.

## Honest limitations

This is a teaching/portfolio project; call it **mesh-routed deferred settlement**, not real-time offline UPI.

1. The receiver can't verify the sender's funds offline. The payment is an IOU until settlement (real UPI Lite uses a pre-funded, hardware-backed wallet).
2. A malicious sender can double-spend offline; only one packet will settle, the other is `REJECTED`.
3. Real BLE mesh is hard (background throttling on Android, locked-down iOS). The mesh here is simulated.
4. Packet existence is metadata a carrier phone can observe.

Not production-ready: demo PINs, in-memory device keys, no NPCI integration, SQLite by default (use Postgres + Redis + KMS for real deployments).

## Ideas to extend

Postgres + Alembic migrations, Prometheus exporter, a real BLE client, mutual-TLS for bridges, offline spending caps tied to pre-funded balances.
