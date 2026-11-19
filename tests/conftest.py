import re

import pytest

from app.config import Rules, Settings
from app.models import Customer
from app.sample_data import sample_products
from app.service import BotService, User
from app.storage.sqlite import SQLiteRepository

ADMIN = "Lucía Fernández"   # admin de la web (ya no es un usuario del chat)
CLIENT = User(2002, "Juana Pérez")
CLIENT_ADDRESS = "Av. Mate de Luna 1234, San Miguel de Tucumán"
TOPE = 10000.0          # total máximo en los tests (más que esto → EN REVISION)
MOTIVO_TOPE = "supera el máximo automático"


def plain(text: str) -> str:
    return re.sub(r"</?(b|i|code)>", "", text)


@pytest.fixture
def settings(tmp_path):
    return Settings(telegram_token="",
                    database_path=str(tmp_path / "trazr.db"), timezone="UTC",
                    # Tope bajo para probar la revisión: «1 fernet» ($ 12.000) la supera.
                    # La revisión del primer pedido se prueba aparte (test_clientes_nuevos.py).
                    rules=Rules(max_order_total=TOPE, review_new_customers=False), ai_retry_delays=(0.0, 0.0))


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


def avisos(repo):
    """Avisos para el admin (los que ve en la web), del más nuevo al más viejo."""
    return [n for n in repo.list_notices()]


def button(out, prefix):
    """Dato del botón que empieza con `prefix` (ej. "cf:") en los mensajes, o None."""
    return next((d for m in out for _, d in m.buttons if d.startswith(prefix)), None)


def pedir(svc, user, text, pago="efectivo"):
    """Manda el pedido y, si el bot muestra el resumen, lo confirma (✅) y elige el medio de pago."""
    out = svc.handle_message(user, text)
    if button(out, "cp:"):   # el resumen ya trae el medio de siempre: se elige el pedido en el test
        out = svc.handle_callback(user, button(out, "cp:"))
    else:
        data = button(out, "cf:")
        if not data:
            return out
        out = svc.handle_callback(user, data)
    data = button(out, "pe:" if pago == "efectivo" else "pt:")
    return svc.handle_callback(user, data) if data else out


def pagar(svc, user, medio="efectivo"):
    """Responde «¿cómo lo vas a pagar?» escribiendo, como haría el cliente."""
    return svc.handle_message(user, medio)
