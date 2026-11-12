"""La capa de Telegram traduce bien entre Update/botones y BotService (sin red)."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app import bot as tg
from app.notifier import Notifier
from app.service import BotService

from .conftest import ADMIN, CLIENT, avisos


def make_context(svc):
    fake_bot = SimpleNamespace(send_message=AsyncMock(), send_chat_action=AsyncMock())
    return SimpleNamespace(application=SimpleNamespace(bot_data={"service": svc}), bot=fake_bot)


def make_update(user, text=None, data=None):
    u = SimpleNamespace(full_name=user.name, username=None, id=user.id)
    cq = None
    if data is not None:
        cq = SimpleNamespace(data=data, answer=AsyncMock(), edit_message_reply_markup=AsyncMock())
    return SimpleNamespace(effective_user=u, effective_chat=SimpleNamespace(id=user.id),
                           effective_message=SimpleNamespace(text=text), callback_query=cq)


def test_mensaje_y_botones(svc: BotService, repo):
    # 1) El cliente pide: le llega el resumen con botones (uno por fila)
    ctx = make_context(svc)
    asyncio.run(tg.on_text(make_update(CLIENT, "1 fernet"), ctx))
    call = ctx.bot.send_message.call_args_list[0].kwargs
    assert call["chat_id"] == CLIENT.id and call["parse_mode"] == "HTML"
    rows = call["reply_markup"].inline_keyboard
    assert [r[0].text for r in rows] == ["✅ Confirmar", "📍 Cambiar dirección", "❌ Cancelar"]

    # 2) Toca Confirmar: le pregunta cómo paga (efectivo / transferencia)
    ctx2 = make_context(svc)
    upd = make_update(CLIENT, data=rows[0][0].callback_data)
    asyncio.run(tg.on_button(upd, ctx2))
    upd.callback_query.edit_message_reply_markup.assert_awaited()
    pay = ctx2.bot.send_message.call_args_list[0].kwargs["reply_markup"].inline_keyboard
    assert [r[0].text for r in pay] == ["💵 Efectivo al recibir", "🏦 Transferencia", "❌ Cancelar"]

    # 3) Elige transferencia: queda en revisión y el aviso va a la web, no al chat
    ctx2 = make_context(svc)
    upd = make_update(CLIENT, data=pay[1][0].callback_data)
    asyncio.run(tg.on_button(upd, ctx2))
    assert {c.kwargs["chat_id"] for c in ctx2.bot.send_message.call_args_list} == {CLIENT.id}
    order = repo.list_orders()[-1]
    assert order.status == "EN REVISION" and avisos(repo)[0].order_id == order.order_id
    assert order.payment_method == "transferencia" and order.payment_status == "pendiente"

    # 3) El admin lo rechaza en la web: el Notifier le manda el aviso al cliente por Telegram
    result = svc.reject(order.order_id, ADMIN, "Sin Fernet hasta el lunes")
    fake_bot = SimpleNamespace(send_message=AsyncMock())
    notifier = Notifier()
    notifier.register("telegram", tg.telegram_sender(fake_bot))
    assert asyncio.run(notifier.send(result.notify)) == 1
    sent = fake_bot.send_message.call_args.kwargs
    assert sent["chat_id"] == CLIENT.id and "Sin Fernet hasta el lunes" in sent["text"]


def test_notifier_sin_canal_no_rompe():
    from app.messages import Outgoing
    assert asyncio.run(Notifier().send([Outgoing(1, "hola", channel="whatsapp")])) == 0
