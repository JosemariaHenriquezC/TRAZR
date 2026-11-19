"""Canal WhatsApp: webhook de Meta simulado, sin llamar a la API real."""
import hashlib
import hmac
import json
import time
from dataclasses import replace

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
# group_seconds=0: en los tests cada mensaje se contesta al instante (la espera se prueba aparte)
CFG = WhatsAppConfig(token="t", phone_number_id="123", app_secret=SECRET, verify_token="trazr-verifica",
                     group_seconds=0)


class FakeGraph:
    """Hace de Graph API: guarda lo que se manda y puede devolver errores."""

    def __init__(self):
        self.sent: list[dict] = []
        self.reads: list[dict] = []        # «leído + escribiendo…» (no son mensajes)
        self.reject: set[str] = set()      # números que contestan «no permitido»

    async def post(self, payload):
        if payload.get("status") == "read":
            self.reads.append(payload)
            return {"success": True}
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
           "timestamp": str(int(time.time())), **msg}
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
    assert "gracias por tu compra" in graph.texts()[-1].lower()


def test_mensaje_repetido_por_meta_se_contesta_una_vez(wa):
    client, graph, _ = wa
    payload = _event({"type": "text", "text": {"body": "/catalogo"}, "id": "wamid.repetido"})
    post(client, payload)
    n = len(graph.sent)
    post(client, payload)
    assert n >= 1 and len(graph.sent) == n


def test_mensaje_atrasado_no_se_contesta(wa):
    # Meta reintenta durante días si el servidor estuvo caído: contestar tarde confunde.
    client, graph, _ = wa
    viejo = str(int(time.time()) - 40 * 60)
    r = post(client, _event({"type": "text", "text": {"body": "hola"}, "timestamp": viejo}))
    assert r.status_code == 200 and graph.sent == [] and graph.reads == []


def test_mensaje_con_poco_atraso_se_contesta(wa):
    client, graph, _ = wa
    reciente = str(int(time.time()) - 5 * 60)
    post(client, _event({"type": "text", "text": {"body": "hola"}, "timestamp": reciente}))
    assert graph.sent


def test_filtro_de_atraso_apagado(svc, settings):
    from app.messages import too_late
    assert too_late(1000.0, 15, now=1000.0 + 16 * 60)
    assert not too_late(1000.0, 15, now=1000.0 + 14 * 60)
    assert not too_late(1000.0, 0, now=1000.0 + 999 * 60)   # 0 = contestar todo
    assert not too_late(0, 15)                               # sin hora: se contesta


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


# ------------------------------------------------------------------ plantillas (ventana de 24 h)
CFG_T = replace(CFG, templates=True)   # con plantillas activadas (opcional, las cobra Meta)


class FakeGraphWindow(FakeGraph):
    """Rechaza los mensajes libres como si el cliente no hubiera escrito en 24 h."""

    def __init__(self, sync_error=True):
        super().__init__()
        self.sync_error = sync_error

    async def post(self, payload):
        if payload.get("status") == "read":
            return {"success": True}
        self.sent.append(payload)
        if payload["type"] != "template" and self.sync_error:
            return {"error": {"code": 131047, "message": "Re-engagement message"}}
        return {"messages": [{"id": f"wamid.{len(self.sent)}"}]}


def _aviso(**kw):
    return Outgoing(int(WA), "✅ <b>¡Tu pedido fue aprobado!</b>", channel="whatsapp",
                    template=("pedido_aprobado", ["P1-AB", "$ 1.000"]), **kw)


def test_aviso_sin_mensaje_reciente_va_con_plantilla():
    import asyncio
    graph = FakeGraphWindow()
    client = WhatsAppClient(CFG_T, post=graph.post)
    asyncio.run(client.send(_aviso()))
    [p] = graph.sent
    assert p["type"] == "template" and p["template"]["name"] == "trazr_pedido_aprobado"
    assert p["template"]["language"]["code"] == "es_AR"
    assert [x["text"] for x in p["template"]["components"][0]["parameters"]] == ["P1-AB", "$ 1.000"]


def test_aviso_con_mensaje_reciente_va_normal_y_si_meta_lo_rechaza_va_la_plantilla():
    import asyncio
    graph = FakeGraphWindow()
    client = WhatsAppClient(CFG_T, post=graph.post)
    client.saw_customer(WA)
    asyncio.run(client.send(_aviso()))
    assert [p["type"] for p in graph.sent] == ["text", "template"]


def test_rechazo_tardio_de_meta_reintenta_con_plantilla(wa):
    """Meta puede aceptar el mensaje y avisar después (estado «failed») que estaba fuera de la ventana."""
    import asyncio
    graph = FakeGraphWindow(sync_error=False)
    client = WhatsAppClient(CFG_T, post=graph.post)
    client.saw_customer(WA)
    asyncio.run(client.send(_aviso()))
    assert [p["type"] for p in graph.sent] == ["text"]
    asyncio.run(client.message_failed("wamid.1", 131047))
    assert [p["type"] for p in graph.sent] == ["text", "template"]
    asyncio.run(client.message_failed("wamid.1", 131047))   # no se manda dos veces
    assert len(graph.sent) == 2


def test_webhook_de_estado_fallido_dispara_la_plantilla(svc, settings):
    graph = FakeGraphWindow(sync_error=False)
    client = WhatsAppClient(CFG_T, post=graph.post)
    app = create_app(svc, Notifier(), settings)
    app.include_router(build_router(svc, CFG_T, client))
    import asyncio
    client.saw_customer(WA)
    asyncio.run(client.send(_aviso()))
    status = {"entry": [{"changes": [{"value": {"statuses": [
        {"id": "wamid.1", "status": "failed", "recipient_id": WA, "errors": [{"code": 131047}]}]}}]}]}
    post(TestClient(app), status)
    assert graph.sent[-1]["type"] == "template"


def test_los_valores_de_la_plantilla_no_llevan_saltos_de_linea():
    client = WhatsAppClient(CFG, post=FakeGraph().post)
    p = client.template_payload(("pedido_cancelado", ["P1", "sin\nstock\t  de coca"]))
    assert p["template"]["components"][0]["parameters"][1]["text"] == "sin stock de coca"


def test_respuestas_del_bot_no_usan_plantilla():
    import asyncio
    graph = FakeGraphWindow()
    client = WhatsAppClient(CFG, post=graph.post)
    asyncio.run(client.send(Outgoing(int(WA), "hola", channel="whatsapp")))
    assert [p["type"] for p in graph.sent] == ["text"]


def test_los_avisos_de_la_web_traen_su_plantilla(svc, repo):
    from .conftest import pedir
    from app.service import User
    nuevo = User(int(WA), "Distribuidora Norte", "whatsapp")
    svc.handle_message(nuevo, "2 coca")
    pedir(svc, nuevo, "Av. Sarmiento 450, Yerba Buena")
    [order] = repo.list_orders()
    r = svc.confirm_payment(order.order_id, ADMIN)
    assert r.notify[0].template == ("gracias_efectivo", [order.order_id])


def test_definiciones_de_plantillas_para_meta():
    from scripts.plantillas_whatsapp import definitions
    from app.whatsapp import PLANTILLAS
    defs = definitions("Trazr", "es_AR")
    assert len(defs) == len(PLANTILLAS) == 5
    for d in defs:
        body = d["components"][0]
        assert d["category"] == "UTILITY" and d["name"].startswith("trazr_")
        assert "{shop}" not in body["text"] and "Trazr" in body["text"]
        assert body["text"].count("{{") == len(body["example"]["body_text"][0])
        assert not body["text"].startswith("{{") and not body["text"].rstrip(".!").endswith("}}")


def test_marca_leido_y_muestra_escribiendo(wa):
    client, graph, _ = wa
    post(client, _event({"type": "text", "text": {"body": "/catalogo"}, "id": "wamid.leeme"}))
    assert graph.reads == [{"messaging_product": "whatsapp", "status": "read", "message_id": "wamid.leeme",
                            "typing_indicator": {"type": "text"}}]


def test_al_cobrar_una_transferencia_agradece_y_avisa_que_sale_el_envio(svc, repo):
    from .conftest import pedir
    from app.service import User
    nuevo = User(int(WA), "Distribuidora Norte", "whatsapp")
    svc.handle_message(nuevo, "2 coca")
    pedir(svc, nuevo, "Av. Sarmiento 450, Yerba Buena", pago="transferencia")
    [order] = repo.list_orders()
    assert order.payment_method == "transferencia"
    [msg] = svc.confirm_payment(order.order_id, ADMIN).notify
    assert "Gracias por tu compra" in msg.text and "pronto estaremos llegando con tu envío" in msg.text
    assert msg.template == ("gracias_transferencia", [order.order_id])


# ------------------------------------------------------------------ sin plantillas (por defecto)
def test_sin_plantillas_el_aviso_fuera_de_las_24h_espera_a_que_el_cliente_escriba(svc, repo, settings):
    """Las plantillas las cobra Meta: vienen apagadas. El aviso se intenta normal; si Meta lo rechaza
    (no se cobra), queda guardado, se avisa en el panel y sale cuando el cliente vuelve a escribir."""
    import asyncio
    assert CFG.templates is False
    graph = FakeGraphWindow()
    client = WhatsAppClient(CFG, post=graph.post)
    held = []
    client.on_held = held.append
    asyncio.run(client.send(_aviso()))
    assert [p["type"] for p in graph.sent] == ["text"]          # ninguna plantilla
    assert len(held) == 1 and len(client.held(WA)) == 1
    # el cliente escribe: primero le llega el aviso pendiente, después la respuesta del bot
    graph.sync_error = False
    app = create_app(svc, Notifier(), settings)
    app.include_router(build_router(svc, CFG, client))
    text(TestClient(app), "hola")
    texts = [p["text"]["body"] for p in graph.sent[1:] if p.get("type") == "text"] + \
            [p["interactive"]["body"]["text"] for p in graph.sent[1:] if p.get("type") == "interactive"]
    assert any("aprobado" in t for t in texts) and client.held(WA) == []
    assert all(p["type"] != "template" for p in graph.sent)


def test_sin_plantillas_un_rechazo_tardio_tambien_queda_pendiente():
    import asyncio
    graph = FakeGraphWindow(sync_error=False)
    client = WhatsAppClient(CFG, post=graph.post)
    asyncio.run(client.send(_aviso()))
    asyncio.run(client.message_failed("wamid.1", 131047))
    assert [p["type"] for p in graph.sent] == ["text"] and len(client.held(WA)) == 1


# ------------------------------------------------------------------ contador del mes
def test_cada_mensaje_enviado_se_cuenta_pero_no_las_plantillas_ni_el_leido():
    import asyncio
    graph = FakeGraph()
    client = WhatsAppClient(CFG, post=graph.post)
    sent = []
    client.on_sent = sent.append
    asyncio.run(client.send(Outgoing(int(WA), "hola", channel="whatsapp")))
    asyncio.run(client.typing("wamid.x"))
    asyncio.run(client._send_template(WA, ("pedido_aprobado", ["P1", "$ 1"])))
    assert sent == [WA]


def test_el_tablero_muestra_los_mensajes_de_whatsapp_del_mes(svc, repo, settings):
    from app.web.main import create_app as _app
    settings.whatsapp = CFG
    settings.secret_key = "x"
    for _ in range(3):
        repo.add_event("wa_enviado", "whatsapp", WA, svc.stamp())
    d = svc.dashboard()
    assert d["whatsapp"]["sent"] == 3 and d["whatsapp"]["free"] == 1000 and d["whatsapp"]["left"] == 997
    assert not d["whatsapp"]["warn"]
    for _ in range(800):
        repo.add_event("wa_enviado", "whatsapp", WA, svc.stamp())
    assert svc.dashboard()["whatsapp"]["warn"]


# ------------------------------------------------------------------ mensajes seguidos
def test_textos_seguidos_se_juntan_y_los_botones_van_aparte():
    from app.whatsapp import Incoming, _merge_texts
    items = [Incoming("1", WA, "N", "text", "hola"), Incoming("2", WA, "N", "text", "quiero 2 birras"),
             Incoming("3", WA, "N", "button", "ct:"), Incoming("4", WA, "N", "text", "la más barata")]
    out = _merge_texts(items)
    assert [(i.kind, i.value) for i in out] == [("text", "hola\nquiero 2 birras"), ("button", "ct:"),
                                                ("text", "la más barata")]
    assert out[0].message_id == "2"


def test_varios_mensajes_seguidos_reciben_una_sola_respuesta(svc, settings):
    # Un cliente manda 4 mensajes casi juntos: se procesan como uno, una sola vez y en orden.
    import asyncio
    import httpx
    from fastapi import FastAPI
    graph = FakeGraph()
    cfg = replace(CFG, group_seconds=0.3)
    client = WhatsAppClient(cfg, post=graph.post)
    app = FastAPI()
    app.include_router(build_router(svc, cfg, client))
    seen = []
    original = svc.handle_message
    svc.handle_message = lambda user, text, on_wait=None: (seen.append(text), original(user, text, on_wait))[1]

    def signed(body_text, n):
        body = json.dumps(_event({"type": "text", "text": {"body": body_text}, "id": f"wamid.seguido{n}"})).encode()
        sig = "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
        return body, {"X-Hub-Signature-256": sig, "Content-Type": "application/json"}

    async def run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as http:
            async def send(i, t):
                await asyncio.sleep(i * 0.05)
                body, headers = signed(t, i)
                return await http.post("/whatsapp/webhook", content=body, headers=headers)
            await asyncio.gather(*(send(i, t) for i, t in
                                   enumerate(["wachin", "quiero 2 cervezas", "de cualquier marca", "ya"])))
    asyncio.run(run())
    assert seen == ["wachin\nquiero 2 cervezas\nde cualquier marca\nya"]
    assert len(graph.reads) == 4          # cada mensaje quedó leído
