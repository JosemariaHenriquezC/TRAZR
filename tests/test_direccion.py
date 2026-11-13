"""Dirección obligatoria, resumen y confirmación antes de registrar el pedido."""
import json
from datetime import datetime, timedelta

from app.models import ESTADO_CONFIRMADO, ESTADO_REVISION
from app.service import User

from .conftest import CLIENT, CLIENT_ADDRESS, avisos, button, pagar, pedir, plain

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
    assert "¿Cómo lo vas a pagar?" in plain(out[0].text) and repo.list_orders() == []
    assert [t for t, _ in out[0].buttons] == ["💵 Efectivo al recibir", "🏦 Transferencia", "❌ Cancelar"]
    out = svc.handle_callback(CLIENT, button(out, "pe:"))
    assert "¡Pedido completado!" in plain(out[0].text) and "pendiente de pago" in plain(out[0].text)
    assert "próximos 60 minutos" in plain(out[0].text) and "efectivo cuando te llega" in plain(out[0].text)
    assert repo.list_orders()[-1].status == ESTADO_CONFIRMADO and repo.get_product("COC1").stock == 48
    assert draft(repo, CLIENT) is None


def test_confirmar_y_cancelar_escribiendo(svc, repo):
    svc.handle_message(CLIENT, "2 coca")
    assert "¿Cómo lo vas a pagar?" in plain(svc.handle_message(CLIENT, "sí, dale")[0].text)
    assert "¡Pedido completado!" in plain(pagar(svc, CLIENT, "en efectivo")[0].text)
    svc.handle_message(CLIENT, "1 fanta")
    assert "descarté" in plain(svc.handle_message(CLIENT, "no")[0].text)
    svc.handle_message(CLIENT, "1 fanta")
    svc.handle_message(CLIENT, "/confirmar")
    assert "¡Pedido completado!" in plain(pagar(svc, CLIENT, "transferencia")[0].text)
    assert len(repo.list_orders()) == 2
    assert "No tengo ningún pedido para confirmar" in plain(svc.handle_message(CLIENT, "/confirmar")[0].text)


def test_un_mensaje_nuevo_reemplaza_el_borrador(svc, repo):
    svc.handle_message(CLIENT, "2 coca")
    out = svc.handle_message(CLIENT, "3 sprite")
    assert "3 × Sprite 2.25L" in plain(out[0].text) and "Coca" not in plain(out[0].text)
    svc.handle_message(CLIENT, "si")
    pagar(svc, CLIENT)
    assert [(l.sku, l.qty) for l in repo.list_orders()[-1].lines] == [("SPR1", 3)]


def test_botones_viejos_no_sirven(svc, repo):
    viejo = button(svc.handle_message(CLIENT, "2 coca"), "cf:")
    svc.handle_message(CLIENT, "3 sprite")  # nuevo resumen, nuevos botones
    assert "ya no está vigente" in plain(svc.handle_callback(CLIENT, viejo)[0].text)
    assert repo.list_orders() == []


def test_resumen_avisa_si_va_a_quedar_en_revision(svc, repo):
    txt = plain(svc.handle_message(CLIENT, "1 fernet")[0].text)
    assert "1 × Fernet 750ml — $ 12.000,00" in txt
    assert "Si lo confirmás, queda en revisión" in txt and "supera el máximo automático" in txt


def test_falta_de_stock_se_avisa_antes_de_pedir_la_direccion(svc, repo):
    out = svc.handle_message(NUEVO, "8 cerveza")
    assert "pediste 8, me quedan 5" in plain(out[0].text)
    out = svc.handle_callback(NUEVO, button(out, "aj:"))
    assert "lo ajusté a lo que hay" in plain(out[0].text) and "¿A qué dirección" in plain(out[0].text)
    out = svc.handle_message(NUEVO, DIR)
    assert "5 × Cerveza Rubia 1L" in plain(out[0].text) and "Revisá tu pedido" in plain(out[0].text)


def test_si_se_vende_el_stock_antes_de_confirmar(svc, repo):
    out = svc.handle_message(CLIENT, "4 cerveza")
    assert "Revisá tu pedido" in plain(out[0].text)
    repo.set_stock("CER1", 2, "s")  # otro cliente se lleva cerveza
    out = svc.handle_callback(CLIENT, button(out, "cf:"))
    txt = plain(out[0].text)
    assert "justo se vendió parte del stock" in txt and "pediste 4, me quedan 2" in txt
    assert repo.list_orders() == []
    svc.handle_message(CLIENT, "si")       # ajusta a 2
    svc.handle_message(CLIENT, "si")       # confirma
    pagar(svc, CLIENT)
    assert [(l.sku, l.qty) for l in repo.list_orders()[-1].lines] == [("CER1", 2)]
    assert repo.get_product("CER1").stock == 0


def test_pedir_otra_cosa_en_vez_de_ajustar(svc, repo):
    svc.handle_message(CLIENT, "10 cerveza")
    out = svc.handle_message(CLIENT, "3 cerveza y 2 coca")  # lista nueva
    assert "Revisá tu pedido" in plain(out[0].text) and "3 × Cerveza Rubia 1L" in plain(out[0].text)
    assert "descarté" in plain(svc.handle_message(CLIENT, "no")[0].text)


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
    assert "¿Cómo lo vas a pagar?" in plain(out[0].text) and repo.list_orders() == []
    out = svc.handle_callback(NUEVO, button(out, "pt:"))
    assert "TRAZR.BEBIDAS.TUC" in plain(out[0].text) and "Transferí $ 9.400,00" in plain(out[0].text)
    o = repo.list_orders()[-1]
    assert "¡Pedido completado!" in plain(out[0].text) and o.address == DIR and o.shipping_cost == 0
    assert o.payment_method == "transferencia" and o.payment_status == "pendiente"

    # El segundo pedido ya no pregunta la dirección: la muestra en el resumen
    assert f"Dirección: {DIR}" in plain(svc.handle_message(NUEVO, "1 fanta")[0].text)


def test_cambiar_direccion_desde_el_resumen(svc, repo):
    out = svc.handle_message(CLIENT, "2 coca")
    out = svc.handle_callback(CLIENT, button(out, "cd:"))
    assert "mandame la nueva dirección" in plain(out[0].text)
    out = svc.handle_message(CLIENT, "Belgrano 300, Tafí Viejo")
    assert "Dirección: Belgrano 300, Tafí Viejo" in plain(out[0].text)
    # Mientras tanto, la dirección guardada NO cambia
    assert repo.get_customer("telegram", str(CLIENT.id)).address == CLIENT_ADDRESS

    out = svc.handle_callback(CLIENT, button(out, "cf:"))
    out = svc.handle_callback(CLIENT, button(out, "pe:"))
    assert repo.list_orders()[-1].address == "Belgrano 300, Tafí Viejo"
    # Después del pedido, pregunta cuál dejar guardada
    q = out[-1]
    assert "¿Qué dirección dejo guardada" in plain(q.text) and CLIENT_ADDRESS in plain(q.text)
    assert [t for t, _ in q.buttons] == ["🏠 La de siempre", "📍 La nueva"]
    out = svc.handle_callback(CLIENT, button(out, "dn:"))
    assert "tus próximos pedidos van a Belgrano 300, Tafí Viejo" in plain(out[0].text)
    assert repo.get_customer("telegram", str(CLIENT.id)).address == "Belgrano 300, Tafí Viejo"


def _pedido_a_otra_direccion(svc):
    svc.handle_message(CLIENT, "2 coca")
    svc.handle_message(CLIENT, "/direccion Belgrano 300, Tafí Viejo")
    svc.handle_message(CLIENT, "si")
    return pagar(svc, CLIENT)


def test_elegir_la_de_siempre(svc, repo):
    out = _pedido_a_otra_direccion(svc)
    out = svc.handle_callback(CLIENT, button(out, "dk:"))
    assert "sigo usando" in plain(out[0].text)
    assert repo.get_customer("telegram", str(CLIENT.id)).address == CLIENT_ADDRESS
    assert f"Dirección: {CLIENT_ADDRESS}" in plain(svc.handle_message(CLIENT, "1 fanta")[0].text)


def test_responder_escribiendo_cual_direccion(svc, repo):
    _pedido_a_otra_direccion(svc)
    assert "próximos pedidos van a Belgrano" in plain(svc.handle_message(CLIENT, "la nueva")[0].text)
    # Ahora la guardada es Belgrano; un pedido a la dirección anterior vuelve a preguntar
    svc.handle_message(CLIENT, "1 sprite")
    svc.handle_message(CLIENT, f"/direccion {CLIENT_ADDRESS}")
    svc.handle_message(CLIENT, "si")
    pagar(svc, CLIENT)
    assert "sigo usando" in plain(svc.handle_message(CLIENT, "la de siempre")[0].text)
    assert repo.get_customer("telegram", str(CLIENT.id)).address == "Belgrano 300, Tafí Viejo"


def test_si_ignora_la_pregunta_queda_la_de_siempre(svc, repo):
    _pedido_a_otra_direccion(svc)
    out = svc.handle_message(CLIENT, "1 fanta")  # no contesta: manda otro pedido
    assert "Revisá tu pedido" in plain(out[0].text) and f"Dirección: {CLIENT_ADDRESS}" in plain(out[0].text)
    assert repo.get_customer("telegram", str(CLIENT.id)).address == CLIENT_ADDRESS


def test_misma_direccion_no_pregunta(svc, repo):
    out = pedir(svc, CLIENT, "2 coca")
    assert len(out) == 1 and not any("¿Qué dirección" in m.text for m in out)


def test_primera_direccion_se_guarda_sola(svc, repo):
    svc.handle_message(NUEVO, "2 coca")
    svc.handle_message(NUEVO, DIR)
    svc.handle_message(NUEVO, "si")
    out = pagar(svc, NUEVO)
    assert not any("¿Qué dirección" in plain(m.text) for m in out)
    assert repo.get_customer("telegram", str(NUEVO.id)).address == DIR


def test_botones_de_la_pregunta_viejos_no_sirven(svc, repo):
    out = _pedido_a_otra_direccion(svc)
    viejo = button(out, "dn:")
    svc.handle_message(CLIENT, "la de siempre")
    assert "ya no está vigente" in plain(svc.handle_callback(CLIENT, viejo)[0].text)
    assert repo.get_customer("telegram", str(CLIENT.id)).address == CLIENT_ADDRESS


def test_direccion_con_comando_durante_el_resumen(svc, repo):
    svc.handle_message(CLIENT, "2 coca")
    out = svc.handle_message(CLIENT, "/direccion Calle Falsa 123, Tafí Viejo")
    assert "Dirección: Calle Falsa 123, Tafí Viejo" in plain(out[0].text)
    svc.handle_message(CLIENT, "si")
    pagar(svc, CLIENT)
    assert repo.list_orders()[-1].address == "Calle Falsa 123, Tafí Viejo"


def test_pedido_con_errores_pide_aclaracion_antes_que_direccion(svc, repo):
    out = svc.handle_message(NUEVO, "3 agua")
    assert "¿cuál de estos?" in plain(out[0].text) and draft(repo, NUEVO) is None


def test_direccion_invalida_se_vuelve_a_pedir(svc, repo):
    svc.handle_message(NUEVO, "2 coca")
    assert "calle y número" in plain(svc.handle_message(NUEVO, "ok")[0].text)
    assert "parece incompleta" in plain(svc.handle_message(NUEVO, "1234567890")[0].text)
    assert "primero necesito la dirección" in plain(svc.handle_message(NUEVO, "¿cuánto tarda en llegar?")[0].text)
    assert repo.list_orders() == [] and draft(repo, NUEVO)["waiting"] == "direccion"
    assert "Revisá tu pedido" in plain(svc.handle_message(NUEVO, DIR)[0].text)


def test_pedido_en_revision_tambien_lleva_direccion(svc, repo):
    svc.handle_message(NUEVO, "1 fernet")
    svc.handle_message(NUEVO, DIR)
    svc.handle_message(NUEVO, "si")
    out = pagar(svc, NUEVO)
    o = repo.list_orders()[-1]
    assert o.status == ESTADO_REVISION and o.address == DIR
    assert all(m.chat_id == NUEVO.id for m in out)  # nada por chat al admin
    assert avisos(repo)[0].kind == "revision" and avisos(repo)[0].order_id == o.order_id


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
    assert "¡Pedido completado!" in plain(pedir(svc, CLIENT, "1 coca")[0].text)
