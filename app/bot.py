"""
Capa de Telegram (python-telegram-bot v21, long polling).

Solo traduce entre Telegram y BotService: no tiene lógica de negocio.
Las llamadas a la base de datos son bloqueantes, por eso se ejecutan en un hilo
aparte (asyncio.to_thread) para no congelar el bot.
"""
from __future__ import annotations

import asyncio
import logging

from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import Forbidden, TelegramError
from telegram.ext import (Application, CallbackQueryHandler, ContextTypes, MessageHandler,
                          filters)

from .config import ConfigError, load_settings
from .service import BotService, Outgoing, User
from .storage import StorageError, build_repository

log = logging.getLogger(__name__)


def _user(update: Update) -> User:
    u = update.effective_user
    name = (u.full_name or u.username or str(u.id)) if u else "desconocido"
    return User(id=update.effective_chat.id, name=name, channel="telegram")


def telegram_sender(bot):
    """Función de envío por Telegram para el Notifier (la usan el bot y la web)."""
    async def send(m: Outgoing) -> None:
        markup = None
        if m.buttons:
            buttons = [InlineKeyboardButton(t, callback_data=d) for t, d in m.buttons]
            # Hasta 2 botones en una fila; más, uno por fila (así no se cortan los textos).
            markup = InlineKeyboardMarkup([buttons] if len(buttons) <= 2 else [[b] for b in buttons])
        try:
            await bot.send_message(chat_id=m.chat_id, text=m.text, parse_mode=ParseMode.HTML,
                                   reply_markup=markup, disable_web_page_preview=True)
        except Forbidden:
            log.warning("El chat %s bloqueó al bot o nunca lo inició; no se pudo avisar.", m.chat_id)
        except TelegramError as exc:
            log.error("No se pudo enviar a %s: %s", m.chat_id, exc)
    return send


async def _send(context: ContextTypes.DEFAULT_TYPE, messages: list[Outgoing]) -> None:
    send = telegram_sender(context.bot)
    for m in messages:
        if m.channel == "telegram":
            await send(m)


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    service: BotService = context.application.bot_data["service"]
    user = _user(update)
    text = update.effective_message.text or ""
    await context.bot.send_chat_action(update.effective_chat.id, "typing")
    out = await asyncio.to_thread(service.handle_message, user, text)
    await _send(context, out)


async def on_other(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(
        "Por ahora solo entiendo mensajes de texto. Escribí tu pedido, por ejemplo: 2 coca, 3 agua sin gas.")


async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    service: BotService = context.application.bot_data["service"]
    query = update.callback_query
    await query.answer()
    out = await asyncio.to_thread(service.handle_callback, _user(update), query.data or "")
    # Se quitan los botones para que no se toquen dos veces.
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except TelegramError:
        pass
    await _send(context, out)


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.exception("Error no controlado", exc_info=context.error)
    if isinstance(update, Update) and update.effective_chat:
        try:
            await context.bot.send_message(update.effective_chat.id,
                                           "Ups, algo falló de mi lado. Probá de nuevo en un momento.")
        except TelegramError:
            pass


async def post_init(app: Application) -> None:
    await app.bot.set_my_commands([
        BotCommand("start", "Empezar"), BotCommand("catalogo", "Bebidas y precios"),
        BotCommand("direccion", "Ver o cambiar mi dirección de envío"),
        BotCommand("confirmar", "Confirmar el pedido del resumen"),
        BotCommand("modificar", "Cambiar mi último pedido"),
        BotCommand("cancelar", "Descartar el pedido sin confirmar"),
        BotCommand("mispedidos", "Mis últimos pedidos"), BotCommand("ayuda", "Ayuda y comandos"),
    ])
    me = await app.bot.get_me()
    log.info("Bot @%s en marcha. Escribile en https://t.me/%s", me.username, me.username)


def main() -> None:
    logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        settings = load_settings(require_telegram=True)
    except ConfigError as exc:
        raise SystemExit(f"Error de configuración: {exc}")

    try:
        repo = build_repository(settings)
    except StorageError as exc:
        raise SystemExit(str(exc))
    # Verificación temprana: si la base de datos no es accesible, se avisa al arrancar.
    try:
        n = len(repo.list_products())
        repo.list_orders()
        log.info("Base de datos OK (%s): %d productos en el catálogo.", settings.database_path, n)
        if n == 0:
            log.warning("El catálogo está vacío. Cargalo con: python -m scripts.init_db --ejemplo "
                        "(o --csv tu_catalogo.csv).")
    except StorageError as exc:
        raise SystemExit(f"No pude acceder a la base de datos: {exc}")
    if settings.ai.usable:
        log.info("Intérprete: Gemini (%s) con respaldo por reglas.", settings.ai.model)
    else:
        log.warning("Gemini APAGADO (falta GEMINI_API_KEY, AI_PARSER_ENABLED=false o falta "
                    "la librería google-genai): los pedidos se interpretan solo por reglas.")

    app = Application.builder().token(settings.telegram_token).post_init(post_init).build()
    app.bot_data["service"] = BotService(repo, settings)
    app.add_handler(MessageHandler(filters.TEXT, on_text))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(MessageHandler(~filters.TEXT & ~filters.StatusUpdate.ALL, on_other))
    app.add_error_handler(on_error)
    asyncio.set_event_loop(asyncio.new_event_loop())
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
