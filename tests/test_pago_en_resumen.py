"""Etapa 3: el medio de pago de siempre va en el resumen y «Confirmar» deja el pedido hecho."""
import pytest

from .conftest import CLIENT, button, plain
from .test_gemini_en_el_bot import _FakeModels, _svc, pedido


def labels(out):
    return [t for m in out for t, _ in m.buttons]


def _primer_pedido(svc, medio="pe:"):
    out = svc.handle_message(CLIENT, "2 coca")
    out = svc.handle_callback(CLIENT, button(out, "cf:"))
    return svc.handle_callback(CLIENT, button(out, medio))


def test_el_primer_pedido_pregunta_como_paga(svc, repo):
    out = svc.handle_message(CLIENT, "2 coca")
    assert labels(out) == ["✅ Confirmar", "📍 Cambiar dirección", "❌ Cancelar"]
    assert "Pago:" not in plain(out[0].text)
    out = svc.handle_callback(CLIENT, button(out, "cf:"))
    assert "¿Cómo lo vas a pagar?" in plain(out[0].text)


def test_el_que_ya_compro_ve_su_pago_y_confirma_de_un_toque(svc, repo):
    _primer_pedido(svc, "pt:")
    out = svc.handle_message(CLIENT, "3 fanta")
    assert "Pago: transferencia (como la última vez)" in plain(out[0].text)
    assert labels(out) == ["✅ Confirmar", "💳 Cambiar pago", "❌ Cancelar"]
    out = svc.handle_callback(CLIENT, button(out, "cf:"))
    assert "¡Pedido completado!" in plain(out[0].text) and "TRAZR" in plain(out[0].text).upper()
    assert len(repo.list_orders()) == 2 and repo.list_orders()[-1].payment_method == "transferencia"


def test_cambiar_pago_desde_el_resumen(svc, repo):
    _primer_pedido(svc, "pe:")
    out = svc.handle_message(CLIENT, "3 fanta")
    out = svc.handle_callback(CLIENT, button(out, "cp:"))
    assert "¿Cómo lo vas a pagar?" in plain(out[0].text)
    svc.handle_callback(CLIENT, button(out, "pt:"))
    assert repo.list_orders()[-1].payment_method == "transferencia"


@pytest.mark.parametrize("texto,medio", [("por transferencia", "transferencia"), ("en efectivo", "efectivo")])
def test_escribir_el_medio_en_el_resumen_confirma_con_ese(svc, repo, texto, medio):
    _primer_pedido(svc, "pt:" if medio == "efectivo" else "pe:")
    svc.handle_message(CLIENT, "3 fanta")
    out = svc.handle_message(CLIENT, texto)
    assert "¡Pedido completado!" in plain(out[0].text)
    assert repo.list_orders()[-1].payment_method == medio


def test_una_pregunta_sobre_el_pago_no_confirma(svc, repo):
    _primer_pedido(svc)
    svc.handle_message(CLIENT, "3 fanta")
    svc.handle_message(CLIENT, "¿puedo pagar por transferencia?")
    assert len(repo.list_orders()) == 1


def test_con_stock_que_se_acabo_no_confirma_aunque_tenga_el_pago(svc, repo):
    _primer_pedido(svc)
    out = svc.handle_message(CLIENT, "4 birras")
    repo.set_stock("CER1", 2, "2026-01-01 00:00:00")
    out = svc.handle_callback(CLIENT, button(out, "cf:"))
    assert "justo se vendió" in plain(out[0].text) and len(repo.list_orders()) == 1


def test_la_modificacion_no_muestra_el_pago(svc, repo):
    _primer_pedido(svc)
    out = svc.handle_message(CLIENT, "/modificar")
    assert "💳 Cambiar pago" not in labels(out)


def test_con_gemini_el_si_confirma_sin_llamarlo(repo, settings):
    models = _FakeModels(pedido([("COC1", "2 coca", 2)]), pedido([("FAN1", "3 fanta", 3)]))
    svc = _svc(repo, settings, models)
    out = svc.handle_message(CLIENT, "2 coca")
    out = svc.handle_callback(CLIENT, button(out, "cf:"))
    svc.handle_callback(CLIENT, button(out, "pe:"))
    svc.handle_message(CLIENT, "3 fanta")
    out = svc.handle_message(CLIENT, "dale")
    assert "¡Pedido completado!" in plain(out[0].text) and len(models.prompts) == 2


def test_productos_despues_de_un_pedido_son_un_pedido_nuevo(repo, settings):
    """Gemini a veces lo toma como cambio al pedido anterior (que todavía se puede modificar):
    si el cliente no dice que cambia algo, es un pedido nuevo con el pago de siempre."""
    from .test_gemini_en_el_bot import intent
    models = _FakeModels(pedido([("COC1", "2 coca", 2)]),
                         intent("cambio_pedido", "Dale", items=[{"sku": "FAN1", "texto": "3 fanta", "cantidad": 3}]))
    svc = _svc(repo, settings, models)
    out = svc.handle_message(CLIENT, "2 coca")
    out = svc.handle_callback(CLIENT, button(out, "cf:"))
    svc.handle_callback(CLIENT, button(out, "pe:"))
    out = svc.handle_message(CLIENT, "3 fanta")
    assert "💳 Cambiar pago" in labels(out) and "Así quedaría tu pedido" not in plain(out[0].text)


def test_agregale_al_pedido_sigue_siendo_un_cambio(repo, settings):
    from .test_gemini_en_el_bot import intent
    models = _FakeModels(pedido([("COC1", "2 coca", 2)]),
                         intent("cambio_pedido", "Dale", items=[{"sku": "COC1", "texto": "2 coca", "cantidad": 2},
                                                                {"sku": "FAN1", "texto": "3 fanta", "cantidad": 3}]))
    svc = _svc(repo, settings, models)
    out = svc.handle_message(CLIENT, "2 coca")
    out = svc.handle_callback(CLIENT, button(out, "cf:"))
    svc.handle_callback(CLIENT, button(out, "pe:"))
    out = svc.handle_message(CLIENT, "agregale 3 fanta al pedido")
    assert "Así quedaría tu pedido" in plain(out[0].text)
