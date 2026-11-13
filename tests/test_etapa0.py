"""Etapa 0 del plan de mejora: errores corregidos y arreglos rápidos (sin APIs reales)."""
import json
from dataclasses import replace

import pytest

from app.config import Rules
from app.parser import parse_order
from app.sample_data import sample_products
from app.service import User
from scripts.evaluar import load_cases, run_case

from .conftest import ADMIN, CLIENT, avisos, button, plain
from .test_gemini_en_el_bot import _FakeModels, _svc, intent, pedido

NUEVO = User(4004, "Almacén Nuevo")


def draft(repo, user):
    found = repo.get_pending_order(user.channel, str(user.id))
    return json.loads(found[0]) if found else None


# ---------------------------------------------------------------- dirección
@pytest.mark.parametrize("texto", ["esperá, agregale 2 fantas", "agregale 3 cocas", "y 2 sprite también"])
def test_un_cambio_en_el_pedido_no_se_toma_como_direccion(svc, repo, texto):
    svc.handle_message(NUEVO, "2 coca")
    out = svc.handle_message(NUEVO, texto)
    assert draft(repo, NUEVO)["waiting"] == "direccion"           # sigue pidiendo la dirección
    assert repo.get_customer(NUEVO.channel, str(NUEVO.id)) is None  # no se guardó basura
    assert "va a" not in plain(out[0].text).lower()


@pytest.mark.parametrize("direccion", ["San Martín 450, Yerba Buena", "Barrio Norte manzana B lote 5",
                                       "Ruta 9 km 1290, Tafí Viejo", "Calle Las Rosas s/n, Banda del Río Salí"])
def test_las_direcciones_reales_se_aceptan(svc, repo, direccion):
    svc.handle_message(NUEVO, "2 coca")
    out = svc.handle_message(NUEVO, direccion)
    assert f"Dirección: {direccion}" in plain(out[0].text)


def test_sin_numero_ni_calle_se_pide_la_direccion_completa(svc, repo):
    svc.handle_message(NUEVO, "2 coca")
    assert "calle y número" in plain(svc.handle_message(NUEVO, "frente a la plaza")[0].text)


# ---------------------------------------------------------------- pago
def _al_pago(svc, user=CLIENT):
    out = svc.handle_message(user, "2 coca")
    return svc.handle_callback(user, button(out, "cf:"))


@pytest.mark.parametrize("texto", ["no tengo plata ahora, cancelá", "cancelalo", "mejor no", "no, dejá"])
def test_cancelar_en_el_pago_no_registra_el_pedido(svc, repo, texto):
    _al_pago(svc)
    out = svc.handle_message(CLIENT, texto)
    assert repo.list_orders() == [] and draft(repo, CLIENT) is None
    assert "descarté" in plain(out[0].text)


@pytest.mark.parametrize("texto,medio", [("efectivo no, transferencia", "transferencia"),
                                         ("por transferencia", "transferencia"),
                                         ("en efectivo cuando llegue", "efectivo"),
                                         ("cash", "efectivo")])
def test_el_medio_de_pago_se_entiende_bien(svc, repo, texto, medio):
    _al_pago(svc)
    svc.handle_message(CLIENT, texto)
    assert repo.list_orders()[-1].payment_method == medio


@pytest.mark.parametrize("texto", ["no sé todavía", "plata", "no por transferencia", "efectivo o transferencia"])
def test_ante_la_duda_vuelve_a_preguntar_con_botones(svc, repo, texto):
    _al_pago(svc)
    out = svc.handle_message(CLIENT, texto)
    assert repo.list_orders() == [] and draft(repo, CLIENT)["waiting"] == "pago"
    assert [t for t, _ in out[0].buttons] == ["💵 Efectivo al recibir", "🏦 Transferencia", "❌ Cancelar"]


# ---------------------------------------------------------------- «sí» y «no» sin Gemini
def test_si_y_no_no_pasan_por_gemini(repo, settings):
    models = _FakeModels(pedido([("COC1", "2 coca", 2)]))
    svc = _svc(repo, settings, models)
    svc.handle_message(CLIENT, "2 coca")
    out = svc.handle_message(CLIENT, "perfecto, confirmalo")
    assert "¿Cómo lo vas a pagar?" in plain(out[0].text)
    assert len(models.prompts) == 1                      # solo el pedido fue a Gemini


# ---------------------------------------------------------------- cajas y packs
@pytest.mark.parametrize("texto", ["2 cajas de coca", "un pack de birras", "3 fardos de agua sin gas", "un six pack de birra"])
def test_sin_caja_cargada_las_cajas_no_se_toman_como_unidades(texto):
    products = [replace(p, pack_size=0) for p in sample_products()]
    [item] = parse_order(texto, products)
    assert item.error == "bulto" and item.qty is None


def test_cajas_preguntan_cuantas_unidades(svc, repo):
    out = svc.handle_message(CLIENT, "2 cajas de sprite")
    assert "¿cuántas unidades de Sprite 2.25L son en total?" in plain(out[0].text)
    assert draft(repo, CLIENT) is None


def test_si_gemini_pone_cantidad_a_una_caja_igual_se_pregunta(repo, settings):
    models = _FakeModels(pedido([("SPR1", "2 cajas de sprite", 2)]))
    svc = _svc(repo, settings, models)
    out = svc.handle_message(CLIENT, "2 cajas de sprite")
    assert "son en total" in plain(out[0].text) and draft(repo, CLIENT) is None


# ---------------------------------------------------------------- palabras en vez de comandos
@pytest.mark.parametrize("texto,esperado", [("catálogo", "Catálogo de bebidas"), ("Mis pedidos", "pedidos"),
                                            ("ayuda", "Cómo pedir")])
def test_palabras_clave(svc, texto, esperado):
    assert esperado in plain(svc.handle_message(CLIENT, texto)[0].text)


def test_los_textos_no_nombran_comandos_con_barra(svc):
    for texto in ("hola", "ayuda", "2 whisky", "2 cajas de sprite"):
        for m in svc.handle_message(CLIENT, texto):
            assert "/catalogo" not in m.text and "/cancelar" not in m.text and "/direccion" not in m.text


# ---------------------------------------------------------------- «lo consulto con el equipo»
def test_si_el_bot_promete_consultar_se_crea_un_aviso(repo, settings):
    models = _FakeModels(intent("consulta", "Buena pregunta, lo consulto con el equipo y te aviso."))
    svc = _svc(repo, settings, models)
    svc.handle_message(CLIENT, "¿hacen factura A?")
    [aviso] = [a for a in avisos(repo) if a.kind == "consulta"]
    assert "¿hacen factura A?" in aviso.detail


# ---------------------------------------------------------------- eventos
def test_los_eventos_no_guardan_el_texto_de_los_mensajes(svc, repo):
    out = svc.handle_message(CLIENT, "2 coca")
    svc.handle_callback(CLIENT, button(out, "cf:"))
    svc.handle_message(CLIENT, "efectivo")
    eventos = repo.list_events()
    tipos = [e["tipo"] for e in eventos]
    assert {"mensaje", "resumen", "boton", "pedido_registrado"} <= set(tipos)
    assert all("coca" not in (e["detalle"] or "").lower() for e in eventos)
    assert all(e["latencia_ms"] is not None for e in eventos if e["tipo"] == "mensaje")


# ---------------------------------------------------------------- set de prueba
def test_el_set_de_prueba_es_valido():
    skus = {p.sku for p in sample_products()}
    keys = {"borrador", "pregunta", "conserva", "sin_pedido", "registrado", "esperando", "no_dice", "dice"}
    cases = load_cases()
    assert len(cases) >= 50 and len({c["id"] for c in cases}) == len(cases)
    for c in cases:
        assert c["turnos"] and set(c["espera"]) <= keys, c["id"]
        for field in ("borrador", "conserva"):
            assert set(c["espera"].get(field, {})) <= skus, c["id"]


@pytest.mark.parametrize("case_id", ["pago-03", "pago-04", "direccion-03", "confirma-03", "confirma-05",
                                     "bulto-01", "consulta-04", "stock-02", "seguridad-02"])
def test_casos_de_la_etapa_0_pasan_sin_gemini(settings, case_id):
    """Los errores corregidos en la etapa 0 no dependen de Gemini: tienen que pasar con reglas."""
    case = next(c for c in load_cases() if c["id"] == case_id)
    ok, fails, _, _ = run_case(case, settings)
    assert ok, fails
