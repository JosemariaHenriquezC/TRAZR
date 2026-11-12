"""Canal WhatsApp: webhook de Meta simulado, sin llamar a la API real."""
import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

from app.config import WhatsAppConfig
from app.messages import Outgoing
from app.models import ESTADO_CONFIRMADO
from app.notifier import Notifier
from app.web.main import create_app
from app.whatsapp import (ERR_NOT_ALLOWED, WhatsAppClient, build_payloads, build_router, parse_webhook,
                          to_whatsapp_text, verify_signature)

from .conftest import ADMIN

WA = "5493815551234"
SECRET = "secreto-de-la-app"
CFG = WhatsAppConfig(token="t", phone_number_id="123", app_secret=SECRET, verify_token="trazr-verifica")


class FakeGraph:
    """Hace de Graph API: guarda lo que se manda y puede devolver errores."""

    def __init__(self):
        self.sent: list[dict] = []
        self.reject: set[str] = set()      # números que contestan «no permitido»

    async def post(self, payload):
        self.sent.append(payload)
        if payload["to"] in self.reject:
            return {"error": {"code": ERR_NOT_ALLOWED, "message": "Recipient not in allowed list"}}
        return {"messages": [{"id": "wamid.out"}]}

    def texts(self):
        out = []
        for p in self.sent:
            out.append(p["text"]["body"] if p["type"] == "text" else p["interactive"]["body"]["text"])
        return out

    def buttons(self):
        """Ids de los botones del último mensaje con botones."""
        for p in reversed(self.sent):
            if p["type"] == "interactive":
                return [b["reply"]["id"] for b in p["interactive"]["action"]["buttons"]]
        return []


@pytest.fixture
def wa(svc, settings):
    graph = FakeGraph()
    client = WhatsAppClient(CFG, post=graph.post)
    notifier = Notifier()
    notifier.register("whatsapp", client.send)
    app = create_app(svc, notifier, settings)
    app.include_router(build_router(svc, CFG, client))
    return TestClient(app), graph, notifier


def _event(msg: dict, wa_id=WA, name="Distribuidora Norte") -> dict:
    msg = {"from": wa_id, "id": msg.pop("id", f"wamid.{hash(json.dumps(msg, sort_keys=True))}"),
           "timestamp": "1700000000", **msg}
    return {"object": "whatsapp_business_account", "entry": [{"id": "1", "changes": [{"field": "messages", "value": {
        "messaging_product": "whatsapp", "metadata": {"phone_number_id": "123"},
        "contacts": [{"wa_id": wa_id, "profile": {"name": name}}], "messages": [msg]}}]}]}


def post(client, payload, secret=SECRET):
    body = json.dumps(payload).encode()
    sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return client.post("/whatsapp/webhook", content=body,
                       headers={"X-Hub-Signature-256": sig, "Content-Type": "application/json"})


def text(client, body, **kw):
    return post(client, _event({"type": "text", "text": {"body": body}}, **kw))


def tap(client, data):
    return post(client, _event({"type": "interactive",
                                "interactive": {"type": "button_reply", "button_reply": {"id": data, "title": "x"}}}))


# ------------------------------------------------------------------ formato
def test_html_del_bot_pasa_a_formato_de_whatsapp():
    assert to_whatsapp_text("<b>Total: $ 1.000</b> y <i>hola</i> <code>P1-AB</code> &amp; más") == \
        "*Total: $ 1.000* y _hola_ `P1-AB` & más"
    assert to_whatsapp_text("<b>Pedido: </b>x") == "*Pedido:* x"   # las marcas pegadas al texto


def test_botones_de_respuesta_con_titulos_cortos():
    m = Outgoing(int(WA), "¿Cómo lo vas a pagar?", [("💵 Efectivo al recibir", "pe:1"),
                                                    ("🏦 Transferencia", "pt:1"), ("❌ Cancelar", "cx:1")])
    [p] = build_payloads(m)
    buttons = p["interactive"]["action"]["buttons"]
    assert [b["reply"]["id"] for b in buttons] == ["pe:1", "pt:1", "cx:1"]
    assert all(len(b["reply"]["title"]) <= 20 for b in buttons)
    assert buttons[0]["reply"]["title"] == "Efectivo al recibir"


def test_resumen_largo_va_aparte_de_los_botones():
    m = Outgoing(1, "• producto\n" * 200, [("✅ Confirmar", "cf:1")])
    payloads = build_payloads(m)
    assert payloads[0]["type"] == "text" and payloads[-1]["type"] == "interactive"
    assert len(payloads[-1]["interactive"]["body"]["text"]) <= 1024


def test_mas_de_tres_opciones_van_como_lista():
    m = Outgoing(1, "Elegí", [(f"Opción {i}", f"o:{i}") for i in range(5)])
    [p] = build_payloads(m)
    assert p["interactive"]["type"] == "list"
    assert len(p["interactive"]["action"]["sections"][0]["rows"]) == 5


def test_firma():
    body = b'{"a":1}'
    good = "sha256=" + hmac.new(b"s", body, hashlib.sha256).hexdigest()
    assert verify_signature("s", body, good)
    assert not verify_signature("s", body + b" ", good)
    assert not verify_signature("s", body, None) and not verify_signature("", body, good)


def test_los_estados_de_entrega_se_ignoran():
    payload = {"entry": [{"changes": [{"value": {"statuses": [{"id": "x", "status": "read"}]}}]}]}
    assert parse_webhook(payload) == []


# ------------------------------------------------------------------ webhook
def test_verificacion_de_meta(wa):
    client, _, _ = wa
    ok = client.get("/whatsapp/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "trazr-verifica",
                                                 "hub.challenge": "1158201444"})
    assert ok.status_code == 200 and ok.text == "1158201444"
    bad = client.get("/whatsapp/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "otra",
                                                  "hub.challenge": "1"})
    assert bad.status_code == 403


def test_firma_invalida_se_descarta(wa):
    client, graph, _ = wa
    r = post(client, _event({"type": "text", "text": {"body": "hola"}}), secret="otra")
    assert r.status_code == 403 and graph.sent == []


def test_webhook_no_pide_sesion_de_admin(wa):
    client, _, _ = wa
    r = text(client, "/catalogo")
    assert r.status_code == 200    # no redirige a /ingresar


def test_pedido_completo_por_whatsapp_y_aviso_de_pago(wa, svc, repo):
    client, graph, notifier = wa
    text(client, "2 coca")
    assert "dirección" in graph.texts()[-1].lower()          # cliente nuevo: primero la dirección
    text(client, "Av. Sarmiento 450, Yerba Buena")
    assert "*Total:" in graph.texts()[-1] and "cf:" in graph.buttons()[0]
    tap(client, graph.buttons()[0])                           # ✅ Confirmar
    assert "pagar" in graph.texts()[-1]
    tap(client, next(b for b in graph.buttons() if b.startswith("pe:")))   # 💵 Efectivo
    assert "Pedido completado" in graph.texts()[-1]

    [order] = repo.list_orders()
    assert order.channel == "whatsapp" and order.customer_id == int(WA)
    assert order.customer_name == "Distribuidora Norte" and order.status == ESTADO_CONFIRMADO
    assert repo.get_customer("whatsapp", WA).address == "Av. Sarmiento 450, Yerba Buena"
    assert all(p["to"] == WA for p in graph.sent)

    # El admin confirma el pago en la web: el aviso sale por WhatsApp.
    result = svc.confirm_payment(order.order_id, ADMIN)
    assert result.ok and result.notify[0].channel == "whatsapp"
    import asyncio
    assert asyncio.run(notifier.send(result.notify)) == 1
    assert "pago" in graph.texts()[-1].lower()


def test_mensaje_repetido_por_meta_se_contesta_una_vez(wa):
    client, graph, _ = wa
    payload = _event({"type": "text", "text": {"body": "/catalogo"}, "id": "wamid.repetido"})
    post(client, payload)
    n = len(graph.sent)
    post(client, payload)
    assert n >= 1 and len(graph.sent) == n


def test_audio_o_foto_pide_texto(wa):
    client, graph, _ = wa
    post(client, _event({"type": "audio", "audio": {"id": "1"}}))
    assert "solo entiendo mensajes de texto" in graph.texts()[-1]


def test_argentina_reintenta_sin_el_9_y_lo_recuerda():
    graph = FakeGraph()
    graph.reject.add(WA)
    client = WhatsAppClient(CFG, post=graph.post)
    import asyncio
    asyncio.run(client.send(Outgoing(int(WA), "hola")))
    assert [p["to"] for p in graph.sent] == [WA, "543815551234"]
    asyncio.run(client.send(Outgoing(int(WA), "otra vez")))
    assert graph.sent[-1]["to"] == "543815551234" and len(graph.sent) == 3
