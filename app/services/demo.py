"""Seeds demo accounts and plays the role of the sender's phone."""
from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from app.crypto.keys import ServerKeyHolder
from app.models import Account
from app.schemas import MeshPacket, SendRequest
from app.sender import SenderWallet, WrongPin

SEED = (
    ("abhishek@demo", "Abhishek", 27_00_000_00, "1234"),   # Rs 27,00,000
    ("mridul@demo", "Mridul", 22_00_000_00, "4321"),       # Rs 22,00,000
    ("mika@demo", "Mika", 50_00_000_00, "1111"),           # Rs 50,00,000
    ("tanupriya@demo", "Tanupriya", 35_00_000_00, "2222"),  # Rs 35,00,000
)
# Accounts from earlier demo versions; removed on start so they don't clutter the dropdowns.
LEGACY_VPAS = ("alice@demo", "bob@demo", "carol@demo", "dave@demo")


class DemoService:
    def __init__(self, session_factory: sessionmaker[Session], keys: ServerKeyHolder, ttl: int):
        self._sf, self._keys, self._ttl = session_factory, keys, ttl
        self.wallets: dict[str, SenderWallet] = {}

    def seed(self) -> None:
        with self._sf() as s, s.begin():
            s.execute(delete(Account).where(Account.vpa.in_(LEGACY_VPAS)))
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
