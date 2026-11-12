import re

import pytest

from app.config import Rules, Settings
from app.models import Customer
from app.sample_data import sample_products
from app.service import BotService, User
from app.storage.sqlite import SQLiteRepository

ADMIN = User(1001, "Admin")
CLIENT = User(2002, "Juana Pérez")
CLIENT_ADDRESS = "Av. Mate de Luna 1234, San Miguel de Tucumán"


def plain(text: str) -> str:
    return re.sub(r"</?(b|i|code)>", "", text)


@pytest.fixture
def settings(tmp_path):
    return Settings(telegram_token="", admin_chat_ids={ADMIN.id},
                    database_path=str(tmp_path / "trazr.db"), timezone="UTC", rules=Rules())


@pytest.fixture
def repo(settings):
    r = SQLiteRepository(settings.database_path)
    for p in sample_products():
        r.upsert_product(p, "2026-01-01 00:00:00")
    # CLIENT ya tiene dirección cargada (el flujo de alta se prueba en test_direccion.py)
    r.save_customer(Customer(CLIENT.channel, str(CLIENT.id), CLIENT.name, CLIENT_ADDRESS),
                    "2026-01-01 00:00:00")
    yield r
    r.close()


@pytest.fixture
def svc(repo, settings):
    return BotService(repo, settings)


def button(out, prefix):
    """Dato del botón que empieza con `prefix` (ej. "cf:") en los mensajes, o None."""
    return next((d for m in out for _, d in m.buttons if d.startswith(prefix)), None)


def pedir(svc, user, text):
    """Manda el pedido y, si el bot muestra el resumen, lo confirma (como tocar ✅)."""
    out = svc.handle_message(user, text)
    data = button(out, "cf:")
    return svc.handle_callback(user, data) if data else out
