"""Etapa 1 (achicada) del plan de mejora: dudas pendientes sin perder lo entendido,
«un par» = 2 y «cambiá X por Y» (sin APIs reales)."""
import json

import pytest

from app.parser import parse_order
from app.sample_data import sample_products
from app.service import User
from scripts.evaluar import load_cases, run_case

from .conftest import CLIENT, button, plain
from .test_gemini_en_el_bot import _FakeModels, _svc, pedido

NUEVO = User(5005, "Kiosco Nuevo")
DIR = "Av. Sarmiento 450, Yerba Buena"


def draft(repo, user=CLIENT):
    found = repo.get_pending_order(user.channel, str(user.id))
    return json.loads(found[0]) if found else None


def labels(out):
    return [t for m in out for t, _ in m.buttons]


def test_con_una_duda_se_anota_lo_demas_y_se_pregunta_solo_eso(svc, repo):
    out = svc.handle_message(CLIENT, "2 cajas de sprite y 3 fantas")
    d = draft(repo)
    assert d["items"] == [["FAN1", 3]] and len(d["pending"]) == 1
    txt = plain(out[0].text)
    assert "3 × Fanta Naranja 2.25L" in txt and "Me falta saber" in txt and "¿cuántas unidades de Sprite" in txt
    assert labels(out) == ["Seguir sin eso", "📍 Cambiar dirección", "❌ Cancelar"]   # no hay ✅ Confirmar


def test_con_una_duda_abierta_no_se_puede_confirmar(svc, repo):
    svc.handle_message(CLIENT, "2 cajas de sprite y 3 fantas")
    out = svc.handle_message(CLIENT, "si")
    assert "Antes de confirmarlo me falta saber" in plain(out[0].text)
    assert repo.list_orders() == [] and draft(repo)["waiting"] == "confirmacion"


def test_un_numero_suelto_completa_la_duda(svc, repo):
    svc.handle_message(CLIENT, "2 cajas de sprite y 3 fantas")
    out = svc.handle_message(CLIENT, "24")
    d = draft(repo)
    assert sorted(d["items"]) == [["FAN1", 3], ["SPR1", 24]] and d["pending"] == []
    assert "✅ Confirmar" in labels(out)


def test_seguir_sin_eso(svc, repo):
    out = svc.handle_message(CLIENT, "2 cajas de sprite y 3 fantas")
    out = svc.handle_callback(CLIENT, button(out, "sp:"))
    assert draft(repo)["items"] == [["FAN1", 3]] and draft(repo)["pending"] == []
    out = svc.handle_callback(CLIENT, button(out, "cf:"))
    svc.handle_callback(CLIENT, button(out, "pe:"))
    [o] = repo.list_orders()
    assert [(l.sku, l.qty) for l in o.lines] == [("FAN1", 3)]


def test_producto_ambiguo_queda_pendiente_y_se_elige_por_nombre(svc, repo):
    svc.handle_message(CLIENT, "2 cocas y 3 aguas")
    assert draft(repo)["items"] == [["COC1", 2]] and draft(repo)["pending"][0]["tipo"] == "ambiguo"
    svc.handle_message(CLIENT, "sin gas")
    assert sorted(draft(repo)["items"]) == [["AGU1", 3], ["COC1", 2]]


def test_cliente_nuevo_con_duda_pide_direccion_y_el_numero_no_es_la_direccion(svc, repo):
    out = svc.handle_message(NUEVO, "2 cajas de sprite y 3 fantas")
    assert draft(repo, NUEVO)["waiting"] == "direccion" and "¿A qué dirección" in plain(out[0].text)
    out = svc.handle_message(NUEVO, "24")
    d = draft(repo, NUEVO)
    assert d["waiting"] == "direccion" and d["pending"] == [] and sorted(d["items"]) == [["FAN1", 3], ["SPR1", 24]]
    assert repo.get_customer(NUEVO.channel, str(NUEVO.id)) is None
    out = svc.handle_message(NUEVO, DIR)
    assert "24 × Sprite 2.25L" in plain(out[0].text) and "✅ Confirmar" in labels(out)


def test_nunca_se_registra_con_una_duda_abierta(svc, repo):
    svc.handle_message(CLIENT, "2 cajas de sprite y 3 fantas")
    found = repo.get_pending_order("telegram", str(CLIENT.id))
    d = json.loads(found[0])
    d["waiting"] = "pago"            # aunque algo lo deje en el paso del pago
    repo.set_pending_order("telegram", str(CLIENT.id), json.dumps(d), found[1])
    svc.handle_message(CLIENT, "efectivo")
    assert repo.list_orders() == []


def test_si_todo_es_duda_no_se_arma_un_borrador_vacio(svc, repo):
    out = svc.handle_message(CLIENT, "2 cajas de sprite")
    assert draft(repo) is None and "son en total" in plain(out[0].text)


def test_con_gemini_tambien_se_conserva_lo_entendido(repo, settings):
    models = _FakeModels(pedido([("SPR1", "2 cajas de sprite", 2), ("FAN1", "3 fantas", 3)]))
    svc = _svc(repo, settings, models)
    svc.handle_message(CLIENT, "2 cajas de sprite y 3 fantas")
    assert draft(repo)["items"] == [["FAN1", 3]]
    assert "falta aclarar: 2 cajas de sprite" in svc._draft_for_ai(svc._get_draft(CLIENT))
    svc.handle_message(CLIENT, "24")              # el número no pasa por Gemini
    assert len(models.prompts) == 1 and sorted(draft(repo)["items"]) == [["FAN1", 3], ["SPR1", 24]]


@pytest.mark.parametrize("texto,esperado", [("un par de fantas", [("FAN1", 2)]), ("par de cocas", [("COC1", 2)]),
                                            ("un par de sprites y media docena de cervezas", [("SPR1", 2), ("CER1", 6)])])
def test_un_par_son_dos(texto, esperado):
    assert [(i.product.sku, i.qty) for i in parse_order(texto, sample_products())] == esperado


def test_el_prompt_explica_cambiar_x_por_y():
    from app.ai_parser import CHAT_PROMPT
    assert "MISMA cantidad" in CHAT_PROMPT and "Un par = 2" in CHAT_PROMPT


@pytest.mark.parametrize("case_id", ["bulto-04", "bulto-06", "bulto-07", "bulto-08", "simple-09", "simple-11",
                                     "duda-02", "duda-03"])
def test_casos_de_la_etapa_1_pasan_sin_gemini(settings, case_id):
    case = next(c for c in load_cases() if c["id"] == case_id)
    ok, fails, _, _ = run_case(case, settings)
    assert ok, fails
