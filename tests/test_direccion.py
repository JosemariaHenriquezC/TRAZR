"""Fase 2: dirección obligatoria y envío siempre incluido (sin costo)."""
from datetime import datetime, timedelta

from app.models import ESTADO_CONFIRMADO, ESTADO_REVISION
from app.service import User

from .conftest import ADMIN, plain

NUEVO = User(3003, "Cliente Nuevo")
DIR = "Av. Sarmiento 450, Yerba Buena"


def test_cliente_sin_direccion_retiene_el_pedido_y_la_pide(svc, repo):
    out = svc.handle_message(NUEVO, "2 coca y 1 sprite")
    assert len(out) == 1 and "¿A qué dirección te lo enviamos?" in plain(out[0].text)
    assert "envío está incluido, sin costo" in plain(out[0].text)
    # Nada se registra ni se descuenta todavía
    assert repo.list_orders() == [] and repo.get_product("COC1").stock == 50
    assert repo.get_pending_order("telegram", str(NUEVO.id))[0] == "2 coca y 1 sprite"

    # El siguiente mensaje es la dirección: se guarda y se procesa el pedido retenido
    out = svc.handle_message(NUEVO, DIR)
    assert "Guardé tu dirección" in plain(out[0].text)
    txt = plain(out[1].text)
    assert "¡Pedido confirmado!" in txt and f"Dirección: {DIR}" in txt and "Total: $ 9.400,00" in txt
    o = repo.list_orders()[-1]
    assert o.status == ESTADO_CONFIRMADO and o.address == DIR and o.shipping_cost == 0
    assert repo.get_customer("telegram", str(NUEVO.id)).address == DIR
    assert repo.get_pending_order("telegram", str(NUEVO.id)) is None
    assert repo.get_product("COC1").stock == 48

    # El segundo pedido ya no pregunta: usa la dirección guardada
    out = svc.handle_message(NUEVO, "1 fanta")
    assert "¡Pedido confirmado!" in plain(out[0].text) and repo.list_orders()[-1].address == DIR


def test_pedido_con_errores_pide_aclaracion_antes_que_direccion(svc, repo):
    out = svc.handle_message(NUEVO, "3 agua")
    assert "¿cuál de estos?" in plain(out[0].text)
    assert repo.get_pending_order("telegram", str(NUEVO.id)) is None


def test_direccion_invalida_se_vuelve_a_pedir(svc, repo):
    svc.handle_message(NUEVO, "2 coca")
    for bad in ("ok", "1234567890"):
        out = svc.handle_message(NUEVO, bad)
        assert "parece incompleta" in plain(out[0].text)
    assert repo.list_orders() == [] and repo.get_pending_order("telegram", str(NUEVO.id))
    out = svc.handle_message(NUEVO, DIR)
    assert "¡Pedido confirmado!" in plain(out[1].text)


def test_pedido_en_revision_tambien_lleva_direccion(svc, repo):
    svc.handle_message(NUEVO, "10 cerveza")
    out = svc.handle_message(NUEVO, DIR)
    o = repo.list_orders()[-1]
    assert o.status == ESTADO_REVISION and o.address == DIR
    admin_msg = next(m for m in out if m.chat_id == ADMIN.id)
    assert f"Dirección: {DIR}" in plain(admin_msg.text)


def test_cancelar_descarta_el_pedido_pendiente(svc, repo):
    svc.handle_message(NUEVO, "2 coca")
    assert "descarté" in plain(svc.handle_message(NUEVO, "/cancelar")[0].text)
    assert repo.get_pending_order("telegram", str(NUEVO.id)) is None
    # El siguiente mensaje vuelve a ser un pedido, no una dirección
    out = svc.handle_message(NUEVO, "1 sprite")
    assert "¿A qué dirección" in plain(out[0].text)
    assert "No tenés ningún pedido" in plain(svc.handle_message(User(9, "X"), "/cancelar")[0].text)


def test_comando_direccion_ver_cambiar_y_completar_pendiente(svc, repo):
    assert "Todavía no tenés dirección" in plain(svc.handle_message(NUEVO, "/direccion")[0].text)
    out = svc.handle_message(NUEVO, f"/direccion {DIR}")
    assert "tus próximos pedidos van a" in plain(out[0].text)
    assert DIR in plain(svc.handle_message(NUEVO, "/direccion")[0].text)
    assert "parece incompleta" in plain(svc.handle_message(NUEVO, "/direccion x")[0].text)
    svc.handle_message(NUEVO, "/direccion Calle Falsa 123, Tafí Viejo")
    svc.handle_message(NUEVO, "1 coca")
    assert repo.list_orders()[-1].address == "Calle Falsa 123, Tafí Viejo"

    # Si hay un pedido esperando, /direccion también lo destraba
    otro = User(4004, "Otro")
    svc.handle_message(otro, "1 coca")
    out = svc.handle_message(otro, f"/direccion {DIR}")
    assert "¡Pedido confirmado!" in plain(out[-1].text)


def test_pendiente_vencido_se_descarta(svc, repo):
    viejo = (datetime.now(svc.tz) - timedelta(hours=25)).strftime("%Y-%m-%d %H:%M:%S")
    repo.set_pending_order("telegram", str(NUEVO.id), "2 coca", viejo)
    # Pasadas 24 h, el mensaje se trata como pedido nuevo (y se vuelve a pedir la dirección)
    out = svc.handle_message(NUEVO, "1 sprite")
    assert "¿A qué dirección" in plain(out[0].text)
    assert repo.get_pending_order("telegram", str(NUEVO.id))[0] == "1 sprite"


def test_clientes_de_distintos_canales_no_se_mezclan(svc, repo):
    svc.handle_message(NUEVO, "/direccion " + DIR)
    wa = User(NUEVO.id, "Mismo número en WhatsApp", channel="whatsapp")
    assert "¿A qué dirección" in plain(svc.handle_message(wa, "1 coca")[0].text)
