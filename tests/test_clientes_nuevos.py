"""Regla B2B: el primer pedido de cada empresa va a revisión; ya verificada, se confirma sola."""
from app.service import User

from .conftest import ADMIN, avisos, pedir, plain

EMPRESA = User(7001, "Distribuidora Norte SRL")


def test_primer_pedido_a_revision_y_despues_se_confirman_solos(svc, repo, settings):
    settings.rules.review_new_customers = True
    settings.rules.max_order_total = 0                      # sin tope: los pedidos B2B son grandes
    svc.handle_message(EMPRESA, "/direccion Ruta 9 km 1290, Tucumán")
    txt = plain(svc.handle_message(EMPRESA, "20 coca")[0].text)
    assert "queda en revisión" in txt and "Primer pedido" in txt   # se lo avisa antes de confirmar
    svc.handle_message(EMPRESA, "si"); svc.handle_message(EMPRESA, "transferencia")
    first = repo.list_orders()[-1]
    assert first.status == "EN REVISION" and "Primer pedido" in first.reason
    assert avisos(repo)[0].kind == "revision"

    # Mientras no pagó ninguno, sigue siendo nueva
    pedir(svc, EMPRESA, "5 sprite")
    assert repo.list_orders()[-1].status == "EN REVISION"

    # El admin la verifica: aprueba y confirma el pago
    assert svc.approve(first.order_id, ADMIN).ok and svc.confirm_payment(first.order_id, ADMIN).ok

    # Ya verificada: un pedido grande se confirma solo y queda por cobrar
    out = pedir(svc, EMPRESA, "30 coca y 20 sprite")
    last = repo.list_orders()[-1]
    assert last.status == "CONFIRMADO" and last.awaiting_payment and last.total > 100000
    assert "¡Pedido completado!" in plain(out[0].text)


def test_regla_apagada(svc, repo, settings):
    settings.rules.review_new_customers = False
    settings.rules.max_order_total = 0
    pedir(svc, User(7002, "Otra SA"), "/direccion Calle 1 123, Tucumán")
    pedir(svc, User(7002, "Otra SA"), "20 coca")
    assert repo.list_orders()[-1].status == "CONFIRMADO"
