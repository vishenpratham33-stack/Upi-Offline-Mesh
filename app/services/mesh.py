"""Software simulation of the Bluetooth mesh: virtual phones + gossip protocol."""
import hashlib
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from app.schemas import IngestResult, MeshPacket
from app.services.ingestion import BridgeIngestionService


def _key(p: MeshPacket) -> str:
    # Dedupe on ciphertext, not packet_id (a relay can rewrite packet_id).
    return hashlib.sha256(p.ciphertext.encode()).hexdigest()


@dataclass
class VirtualDevice:
    device_id: str
    has_internet: bool = False
    packets: dict[str, MeshPacket] = field(default_factory=dict)


class MeshSimulator:
    DEFAULT_DEVICES = (
        ("phone-sender", False),
        ("phone-stranger1", False),
        ("phone-stranger2", False),
        ("phone-bridge", True),
        ("phone-bridge-2", True),  # a 2nd bridge makes the duplicate-storm visible out of the box
    )

    def __init__(self, ingestion: BridgeIngestionService, initial_ttl: int):
        self._ingestion = ingestion
        self._initial_ttl = initial_ttl
        self._lock = threading.RLock()
        self._flow_lock = threading.Lock()
        self._devices: dict[str, VirtualDevice] = {}
        self.reset()

    def reset(self) -> None:
        with self._lock:
            self._devices = {d: VirtualDevice(d, net) for d, net in self.DEFAULT_DEVICES}

    def inject(self, device_id: str, packet: MeshPacket) -> None:
        with self._lock:
            if device_id not in self._devices:
                raise KeyError(device_id)
            self._devices[device_id].packets[_key(packet)] = packet

    def gossip_round_detailed(self) -> list[dict]:
        """One hop: every device pushes each packet (ttl>0) to every device lacking it.
        Returns the individual transfers so a UI can animate them."""
        with self._lock:
            snapshot = [(d, list(d.packets.values())) for d in self._devices.values()]
            transfers: list[dict] = []
            for src, packets in snapshot:
                for p in packets:
                    if p.ttl <= 0:
                        continue
                    fwd = p.model_copy(update={"ttl": p.ttl - 1})
                    for dst in self._devices.values():
                        if dst is not src and _key(p) not in dst.packets:
                            dst.packets[_key(p)] = fwd
                            transfers.append({"from": src.device_id, "to": dst.device_id, "ttl": fwd.ttl})
            return transfers

    def gossip_round(self) -> int:
        return len(self.gossip_round_detailed())

    def run_payment_flow(self, device_id: str, packet: MeshPacket) -> dict:
        """The whole demo in one call: fresh mesh -> inject -> gossip until it stops spreading
        -> bridges upload in parallel. Returns a trace the dashboard replays as an animation.
        Serialised so two visitors' payments can't interleave on the shared demo mesh."""
        with self._flow_lock:
            self.reset()
            self.inject(device_id, packet)
            rounds: list[list[dict]] = []
            for _ in range(self._initial_ttl + 1):
                transfers = self.gossip_round_detailed()
                if not transfers:
                    break
                rounds.append(transfers)
            uploads = self.flush_bridges()
            return {
                "packet_id": packet.packet_id,
                "origin": device_id,
                "devices": [{"device_id": d["device_id"], "has_internet": d["has_internet"]}
                            for d in self.state()],
                "rounds": rounds,
                "uploads": uploads,
            }

    def flush_bridges(self) -> list[dict]:
        """Every internet-connected device uploads all packets it holds, in parallel."""
        with self._lock:
            jobs = [
                (d.device_id, p)
                for d in self._devices.values()
                if d.has_internet
                for p in d.packets.values()
            ]
            for d in self._devices.values():
                if d.has_internet:
                    d.packets.clear()

        def upload(job: tuple[str, MeshPacket]) -> dict:
            node, packet = job
            try:
                res: IngestResult = self._ingestion.ingest(
                    packet, node, self._initial_ttl - packet.ttl
                )
                return {"bridge": node, "packet_id": packet.packet_id, **res.model_dump(mode="json")}
            except Exception as exc:  # one failing upload must not abort the others
                return {"bridge": node, "packet_id": packet.packet_id, "outcome": "ERROR", "reason": str(exc)}

        if not jobs:
            return []
        with ThreadPoolExecutor(max_workers=min(16, len(jobs))) as pool:
            return list(pool.map(upload, jobs))

    def state(self) -> list[dict]:
        with self._lock:
            return [
                {
                    "device_id": d.device_id,
                    "has_internet": d.has_internet,
                    "packet_count": len(d.packets),
                    "packets": [
                        {"packet_id": p.packet_id, "ttl": p.ttl, "hash": _key(p)[:12]}
                        for p in d.packets.values()
                    ],
                }
                for d in self._devices.values()
            ]
