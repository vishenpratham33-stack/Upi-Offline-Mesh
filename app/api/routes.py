from fastapi import APIRouter, Header, HTTPException, Request, Response
from sqlalchemy import select

from app.container import Container
from app.models import Account, Transaction
from app.schemas import IngestResult, MeshPacket, SendRequest
from app.sender import WrongPin

router = APIRouter(prefix="/api")


def _c(request: Request) -> Container:
    return request.app.state.container


@router.get("/health")
def health():
    return {"status": "ok"}


@router.get("/server-key")
def server_key(request: Request):
    return {"algorithm": "RSA-2048/OAEP-SHA256", "public_key_pem": _c(request).keys.public_key_pem()}


@router.get("/accounts")
def accounts(request: Request):
    with _c(request).session_factory() as s:
        rows = s.scalars(select(Account).order_by(Account.id))
        return [{"vpa": a.vpa, "name": a.name, "balance_paise": a.balance_paise} for a in rows]


@router.get("/transactions")
def transactions(request: Request, limit: int = 20):
    limit = max(1, min(limit, 100))
    with _c(request).session_factory() as s:
        rows = s.scalars(select(Transaction).order_by(Transaction.id.desc()).limit(limit))
        return [
            {
                "id": t.id, "sender": t.sender_vpa, "receiver": t.receiver_vpa,
                "amount_paise": t.amount_paise, "status": t.status, "reason": t.reason,
                "bridge": t.bridge_node_id, "hops": t.hop_count,
                "packet_hash": t.packet_hash[:12], "at": t.created_at.isoformat(),
            }
            for t in rows
        ]


@router.get("/metrics")
def metrics(request: Request):
    c = _c(request)
    return {"outcomes": c.metrics.snapshot(), "idempotency_entries": len(c.idempotency)}


@router.get("/mesh/state")
def mesh_state(request: Request):
    return _c(request).mesh.state()


@router.post("/demo/send")
def demo_send(body: SendRequest, request: Request):
    c = _c(request)
    try:
        packet = c.demo.create_packet(body)
        c.mesh.inject(body.device_id, packet)
    except WrongPin:
        raise HTTPException(403, "incorrect PIN")
    except KeyError as exc:
        raise HTTPException(404, f"unknown sender or device: {exc.args[0]}")
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    return {"packet_id": packet.packet_id, "ttl": packet.ttl, "injected_into": body.device_id}


@router.post("/mesh/gossip")
def gossip(request: Request):
    return {"transfers": _c(request).mesh.gossip_round()}


@router.post("/mesh/flush")
def flush(request: Request):
    return {"results": _c(request).mesh.flush_bridges()}


@router.post("/mesh/reset")
def reset(request: Request):
    c = _c(request)
    c.mesh.reset()
    c.idempotency.clear()
    return {"status": "reset"}


@router.post("/bridge/ingest", response_model=IngestResult)
def ingest(
    packet: MeshPacket,
    request: Request,
    response: Response,
    x_bridge_node_id: str = Header(default="unknown", max_length=64),
    x_hop_count: int | None = Header(default=None, ge=0, le=64),
):
    """The production endpoint: real bridge phones POST packets here."""
    c = _c(request)
    if not c.limiter.allow(x_bridge_node_id):
        raise HTTPException(429, "bridge rate limit exceeded", headers={"Retry-After": "10"})
    try:
        return c.ingestion.ingest(packet, x_bridge_node_id, x_hop_count)
    except Exception:
        raise HTTPException(503, "temporary failure, retry the upload")
