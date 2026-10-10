import base64

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.container import Container, build_container
from app.main import create_app
from app.schemas import MeshPacket


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(database_url=f"sqlite:///{tmp_path/'t.db'}", _env_file=None)


@pytest.fixture
def container(settings) -> Container:
    return build_container(settings)


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as c:
        yield c


def make_packet(container: Container, sender="abhishek@demo", receiver="mridul@demo", rupees=500,
                pin="1234", **kw) -> MeshPacket:
    wallet = container.demo.wallets[sender]
    return wallet.create_packet(container.keys.public_key, receiver, rupees * 100, pin,
                                container.settings.packet_ttl, **kw)


def balance(container: Container, vpa: str) -> int:
    from sqlalchemy import select
    from app.models import Account
    with container.session_factory() as s:
        return s.scalar(select(Account.balance_paise).where(Account.vpa == vpa))


def flip_byte(packet: MeshPacket, index: int) -> MeshPacket:
    raw = bytearray(base64.b64decode(packet.ciphertext))
    raw[index] ^= 0x01
    return packet.model_copy(update={"ciphertext": base64.b64encode(bytes(raw)).decode()})


def set_balance(container: Container, vpa: str, paise: int) -> None:
    from sqlalchemy import update
    from app.models import Account
    with container.session_factory() as s, s.begin():
        s.execute(update(Account).where(Account.vpa == vpa).values(balance_paise=paise))
