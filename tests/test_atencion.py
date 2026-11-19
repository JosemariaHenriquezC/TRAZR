"""Atención por una persona: el cliente pide hablar con alguien, el bot se calla, el admin le
escribe desde el panel y lo devuelve al bot. La charla vive en memoria, no en la base."""
import pytest

from app.messages import HANDOFF_MSG, RELEASE_MSG

from .conftest import CLIENT, plain
from .test_web import admin, client, csrf, tg, text  # noqa: F401  (fixtures)

KEY = f"{CLIENT.channel}:{CLIENT.id}"


@pytest.mark.parametrize("frase", ["quiero hablar con una persona", "me pasás con un asesor?",
                                   "necesito hablar con alguien por favor", "atención al cliente"])
def test_pedir_una_persona_pausa_al_bot_y_avisa_en_el_panel(svc, repo, frase):
    out = svc.handle_message(CLIENT, frase)
    assert plain(out[0].text) == HANDOFF_MSG
    assert repo.get_open_handoff(CLIENT.channel, str(CLIENT.id)) is not None
    assert any(n.kind == "atencion" for n in repo.list_notices())
    # mientras lo atiende una persona, el bot no contesta (ni pedidos ni saludos)
    assert svc.handle_message(CLIENT, "2 coca") == [] and svc.handle_message(CLIENT, "hola") == []
    assert [rol for rol, _, _ in svc.conversation(CLIENT.channel, str(CLIENT.id))] == ["cliente", "bot", "cliente", "cliente"]


def test_una_frase_cualquiera_no_pasa_a_una_persona(svc, repo):
    for frase in ("2 coca", "hola", "¿hasta qué hora reparten?", "la persona que recibe es Juan"):
        svc.handle_message(CLIENT, frase)
    assert repo.list_open_handoffs() == []


def test_la_charla_no_se_guarda_en_la_base(svc, repo):
    svc.handle_message(CLIENT, "quiero hablar con una persona")
    svc.handle_message(CLIENT, "es por una factura")
    import sqlite3
    db = sqlite3.connect(repo.path if hasattr(repo, "path") else svc.s.database_path)
    dump = "\n".join(db.iterdump())
    assert "es por una factura" not in dump and "quiero hablar" not in dump


def test_el_admin_responde_y_devuelve_al_bot_desde_el_panel(svc, repo, admin, tg):
    svc.handle_message(CLIENT, "quiero hablar con una persona")
    svc.handle_message(CLIENT, "es por una factura A")
    page = admin.get("/admin/atencion")
    t = text(page)
    assert "Atención" in t and "es por una factura A" in t and "Pidió hablar con una persona" in t
    assert '<span class="count"' in page.text      # contador en el menú
    r = admin.post("/admin/atencion/responder", data={"c": KEY, "texto": "Hola, sí, hacemos factura A <b>",
                                                      "csrf_token": csrf(page.text)})
    assert "Mensaje enviado" in r.text
    assert tg.sent[-1].chat_id == CLIENT.id and "factura A &lt;b&gt;" in tg.sent[-1].text
    assert "Equipo" in r.text and "hacemos factura A" in r.text
    r = admin.post("/admin/atencion/devolver", data={"c": KEY, "csrf_token": csrf(r.text)})
    assert "vuelve a atender" in r.text and plain(tg.sent[-1].text) == RELEASE_MSG
    assert repo.list_open_handoffs() == []
    assert svc.handle_message(CLIENT, "2 coca")      # el bot vuelve a contestar


def test_el_admin_puede_tomar_la_charla_de_un_cliente(svc, repo, admin):
    svc.handle_message(CLIENT, "2 coca")
    page = admin.get(f"/admin/clientes?c={KEY}")
    assert "Atender yo" in page.text
    admin.post("/admin/atencion/tomar", data={"c": KEY, "csrf_token": csrf(page.text)})
    assert repo.get_open_handoff(CLIENT.channel, str(CLIENT.id)) is not None
    assert svc.handle_message(CLIENT, "y 3 fanta") == []


def test_un_cliente_invalido_no_rompe_nada(admin):
    page = admin.get("/admin/atencion")
    r = admin.post("/admin/atencion/responder", data={"c": "x:abc", "texto": "hola", "csrf_token": csrf(page.text)})
    assert "No pude enviar el mensaje" in r.text
