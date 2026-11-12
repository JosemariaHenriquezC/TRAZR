"""Dirección obligatoria, resumen y confirmación antes de registrar el pedido."""
import json
from datetime import datetime, timedelta

from app.models import ESTADO_CONFIRMADO, ESTADO_REVISION
from app.service import User

from .conftest import ADMIN, CLIENT, CLIENT_ADDRESS, button, pedir, plain

NUEVO = User(3003, "Cliente Nuevo")
DIR = "Av. Sarmiento 450, Yerba Buena"


def draft(repo, user):
    found = repo.get_pending_order("telegram", str(user.id))
    return json.loads(found[0]) if found else None


# ---------------------------------------------------------- confirmación
def test_cliente_con_direccion_ve_resumen_y_nada_se_registra_hasta_confirmar(svc, repo):
    out = svc.handle_message(CLIENT, "2 coca y 1 sprite")
    txt = plain(out[0].text)
    assert "Revisá tu pedido" in txt and "2 × Coca-Cola 2.25L — $ 3.200,00 c/u = $ 6.400,00" in txt
    assert f"Dirección: {CLIENT_ADDRESS}" in txt and "Envío: incluido, sin costo" in txt
    assert "Total: $ 9.400,00" in txt and "¿Lo confirmo?" in txt
    assert [t for t, _ in out[0].buttons] == ["✅ Confirmar", "📍 Cambiar dirección", "❌ Cancelar"]
    assert repo.list_orders() == [] and repo.get_product("COC1").stock == 50

    out = svc.handle_callback(CLIENT, button(out, "cf:"))
    assert "¡Pedido confirmado!" in plain(out[0].text) and "próximos 60 minutos" in plain(out[0].text)
    assert repo.list_orders()[-1].status == ESTADO_CONFIRMADO and repo.get_product("COC1").stock == 48
    assert draft(repo, CLIENT) is None


def test_confirmar_y_cancelar_escribiendo(svc, repo):
    svc.handle_message(CLIENT, "2 coca")
    assert "¡Pedido confirmado!" in plain(svc.handle_message(CLIENT, "sí, dale")[0].text)
    svc.handle_message(CLIENT, "1 fanta")
    assert "descarté" in plain(svc.handle_message(CLIENT, "no")[0].text)
    svc.handle_message(CLIENT, "1 fanta")
    assert "¡Pedido confirmado!" in plain(svc.handle_message(CLIENT, "/confirmar")[0].text)
    assert len(repo.list_orders()) == 2
    assert "No tengo ningún pedido para confirmar" in plain(svc.handle_message(CLIENT, "/confirmar")[0].text)


def test_un_mensaje_nuevo_reemplaza_el_borrador(svc, repo):
    svc.handle_message(CLIENT, "2 coca")
    out = svc.handle_message(CLIENT, "3 sprite")
    assert "3 × Sprite 2.25L" in plain(out[0].text) and "Coca" not in plain(out[0].text)
    svc.handle_message(CLIENT, "si")
    assert [(l.sku, l.qty) for l in repo.list_orders()[-1].lines] == [("SPR1", 3)]


def test_botones_viejos_no_sirven(svc, repo):
    viejo = button(svc.handle_message(CLIENT, "2 coca"), "cf:")
    svc.handle_message(CLIENT, "3 sprite")  # nuevo resumen, nuevos botones
    assert "ya no está vigente" in plain(svc.handle_callback(CLIENT, viejo)[0].text)
    assert repo.list_orders() == []


def test_resumen_avisa_si_va_a_quedar_en_revision(svc, repo):
    txt = plain(svc.handle_message(CLIENT, "10 cerveza")[0].text)
    assert "Si lo confirmás, queda en revisión" in txt and "Stock insuficiente: Cerveza Rubia 1L" in txt
    txt = plain(svc.handle_message(CLIENT, "1 fernet")[0].text)
    assert "Fernet 750ml — precio a confirmar" in txt


# ------------------------------------------------------------- dirección
def test_cliente_sin_direccion_la_pide_y_despues_muestra_el_resumen(svc, repo):
    out = svc.handle_message(NUEVO, "2 coca y 1 sprite")
    assert len(out) == 1 and "¿A qué dirección te lo enviamos?" in plain(out[0].text)
    assert draft(repo, NUEVO)["items"] == [["COC1", 2], ["SPR1", 1]]

    out = svc.handle_message(NUEVO, DIR)
    txt = plain(out[0].text)
    assert f"va a {DIR}" in txt and "Revisá tu pedido" in txt and "Total: $ 9.400,00" in txt
    assert repo.get_customer("telegram", str(NUEVO.id)).address == DIR
    assert repo.list_orders() == []  # todavía falta confirmar

    out = svc.handle_callback(NUEVO, button(out, "cf:"))
    o = repo.list_orders()[-1]
    assert "¡Pedido confirmado!" in plain(out[0].text) and o.address == DIR and o.shipping_cost == 0

    # El segundo pedido ya no pregunta la dirección: la muestra en el resumen
    assert f"Dirección: {DIR}" in plain(svc.handle_message(NUEVO, "1 fanta")[0].text)


def test_cambiar_direccion_desde_el_resumen(svc, repo):
    out = svc.handle_message(CLIENT, "2 coca")
    out = svc.handle_callback(CLIENT, button(out, "cd:"))
    assert "mandame la nueva dirección" in plain(out[0].text)
    out = svc.handle_message(CLIENT, "Belgrano 300, Tafí Viejo")
    assert "Dirección: Belgrano 300, Tafí Viejo" in plain(out[0].text)
    svc.handle_callback(CLIENT, button(out, "cf:"))
    assert repo.list_orders()[-1].address == "Belgrano 300, Tafí Viejo"
    assert repo.get_customer("telegram", str(CLIENT.id)).address == "Belgrano 300, Tafí Viejo"


def test_direccion_con_comando_durante_el_resumen(svc, repo):
    svc.handle_message(CLIENT, "2 coca")
    out = svc.handle_message(CLIENT, "/direccion Calle Falsa 123, Tafí Viejo")
    assert "Dirección: Calle Falsa 123, Tafí Viejo" in plain(out[0].text)
    svc.handle_message(CLIENT, "si")
    assert repo.list_orders()[-1].address == "Calle Falsa 123, Tafí Viejo"


def test_pedido_con_errores_pide_aclaracion_antes_que_direccion(svc, repo):
    out = svc.handle_message(NUEVO, "3 agua")
    assert "¿cuál de estos?" in plain(out[0].text) and draft(repo, NUEVO) is None


def test_direccion_invalida_se_vuelve_a_pedir(svc, repo):
    svc.handle_message(NUEVO, "2 coca")
    for bad in ("ok", "1234567890"):
        assert "parece incompleta" in plain(svc.handle_message(NUEVO, bad)[0].text)
    assert "primero necesito la dirección" in plain(svc.handle_message(NUEVO, "¿cuánto tarda en llegar?")[0].text)
    assert repo.list_orders() == [] and draft(repo, NUEVO)["waiting"] == "direccion"
    assert "Revisá tu pedido" in plain(svc.handle_message(NUEVO, DIR)[0].text)


def test_pedido_en_revision_tambien_lleva_direccion(svc, repo):
    svc.handle_message(NUEVO, "10 cerveza")
    svc.handle_message(NUEVO, DIR)
    out = svc.handle_message(NUEVO, "si")
    o = repo.list_orders()[-1]
    assert o.status == ESTADO_REVISION and o.address == DIR
    admin_msg = next(m for m in out if m.chat_id == ADMIN.id)
    assert f"Dirección: {DIR}" in plain(admin_msg.text)


def test_cancelar_mientras_espera_la_direccion(svc, repo):
    svc.handle_message(NUEVO, "2 coca")
    assert "descarté" in plain(svc.handle_message(NUEVO, "cancelar")[0].text)
    assert draft(repo, NUEVO) is None
    svc.handle_message(NUEVO, "1 sprite")
    assert "descarté" in plain(svc.handle_message(NUEVO, "/cancelar")[0].text)
    assert "No tenés ningún pedido" in plain(svc.handle_message(User(9, "X"), "/cancelar")[0].text)


def test_comando_direccion_sin_pedido(svc, repo):
    assert "Todavía no tenés dirección" in plain(svc.handle_message(NUEVO, "/direccion")[0].text)
    assert "tus próximos pedidos van a" in plain(svc.handle_message(NUEVO, f"/direccion {DIR}")[0].text)
    assert DIR in plain(svc.handle_message(NUEVO, "/direccion")[0].text)
    assert "parece incompleta" in plain(svc.handle_message(NUEVO, "/direccion x")[0].text)


def test_borrador_vencido_se_descarta(svc, repo):
    viejo = (datetime.now(svc.tz) - timedelta(hours=25)).strftime("%Y-%m-%d %H:%M:%S")
    repo.set_pending_order("telegram", str(NUEVO.id), "2 coca", viejo)
    out = svc.handle_message(NUEVO, "1 sprite")  # es un pedido nuevo, no una dirección
    assert "¿A qué dirección" in plain(out[0].text) and draft(repo, NUEVO)["text"] == "1 sprite"


def test_pendientes_de_la_fase_2_siguen_funcionando(svc, repo):
    repo.set_pending_order("telegram", str(NUEVO.id), "2 coca", svc.stamp())  # solo texto
    out = svc.handle_message(NUEVO, DIR)
    assert "2 × Coca-Cola 2.25L" in plain(out[0].text)
    repo.set_pending_order("telegram", "77", json.dumps({"texto": "1 fanta", "items": [["FAN1", 1]],
                                                         "desconocidos": []}), svc.stamp())
    out = svc.handle_message(User(77, "Viejo"), DIR)
    assert "1 × Fanta Naranja 2.25L" in plain(out[0].text)


def test_clientes_de_distintos_canales_no_se_mezclan(svc, repo):
    wa = User(CLIENT.id, "Mismo número en WhatsApp", channel="whatsapp")
    assert "¿A qué dirección" in plain(svc.handle_message(wa, "1 coca")[0].text)


def test_helper_pedir(svc, repo):
    assert "¡Pedido confirmado!" in plain(pedir(svc, CLIENT, "1 coca")[0].text)
