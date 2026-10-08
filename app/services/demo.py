"""Seeds demo accounts and plays the role of the sender's phone."""
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.crypto.keys import ServerKeyHolder
from app.models import Account
from app.schemas import MeshPacket, SendRequest
from app.sender import SenderWallet, WrongPin

SEED = (
    ("alice@demo", "Alice", 100_000_00, "1234"),
    ("bob@demo", "Bob", 50_000_00, "4321"),
    ("carol@demo", "Carol", 25_000_00, "1111"),
    ("dave@demo", "Dave", 10_000_00, "2222"),
)


class DemoService:
    def __init__(self, session_factory: sessionmaker[Session], keys: ServerKeyHolder, ttl: int):
        self._sf, self._keys, self._ttl = session_factory, keys, ttl
        self.wallets: dict[str, SenderWallet] = {}

    def seed(self) -> None:
        with self._sf() as s, s.begin():
            for vpa, name, paise, pin in SEED:
                wallet = SenderWallet.create(vpa, pin)
                self.wallets[vpa] = wallet
                existing = s.scalar(select(Account).where(Account.vpa == vpa))
                if existing:  # persistent DB: re-register this run's device key
                    existing.device_public_key = wallet.public_key_b64
                else:
                    s.add(Account(vpa=vpa, name=name, balance_paise=paise,
                                  device_public_key=wallet.public_key_b64))

    def create_packet(self, req: SendRequest) -> MeshPacket:
        wallet = self.wallets.get(req.sender)
        if wallet is None:
            raise KeyError(req.sender)
        paise = int((req.amount * 100).to_integral_value())
        if Decimal(paise) != req.amount * 100:
            raise ValueError("amount has more than 2 decimal places")
        return wallet.create_packet(self._keys.public_key, req.receiver, paise, req.pin, self._ttl)


__all__ = ["DemoService", "WrongPin"]
