"""
Configuración del bot.

Todo lo sensible (token de Telegram, claves de APIs) se lee de
variables de entorno o de un archivo .env (ver .env.example). Nada de eso
está escrito en el código.

Las reglas de negocio ("dentro de los parámetros") también son configurables
desde el .env, así se pueden ajustar sin tocar código.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv


class ConfigError(Exception):
    """Falta o es inválida una variable de configuración."""


def _bool(value: str | None, default: bool) -> bool:
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "si", "sí", "yes", "y"}


def _int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if raw == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} debe ser un número entero (valor actual: {raw!r})") from exc


def _float(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip().replace(",", ".")
    if raw == "":
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} debe ser un número (valor actual: {raw!r})") from exc


@dataclass
class Rules:
    """
    Regla mínima de "pedido dentro de los parámetros".

    Un pedido se CONFIRMA AUTOMÁTICAMENTE solo si TODOS sus renglones cumplen:
      1. El producto existe en el catálogo (base de datos) y está activo.
      2. Tiene precio vigente: Precio > 0 y (Vigente_hasta vacío o >= hoy).
      3. Hay stock suficiente: Stock >= cantidad pedida.
    Y además, opcionalmente:
      4. El total no supera MAX_ORDER_TOTAL (0 = sin límite).

    Si falla 2, 3 o 4 → el pedido se registra "EN REVISIÓN" y se deriva al admin.
    Si falla 1 → depende de ON_UNKNOWN_PRODUCT:
        "ask"    (default) se le pide al cliente que corrija; no se registra.
        "review" se registra en revisión igual.

    Errores de interpretación (mensaje ambiguo, cantidad inválida o mayor a
    MAX_QTY_PER_ITEM) nunca se registran: el bot pide aclaración.
    """

    require_price_valid: bool = True
    require_stock: bool = True
    max_order_total: float = 0.0          # 0 = sin límite
    max_qty_per_item: int = 1000          # cantidades mayores se consideran inválidas
    on_unknown_product: str = "ask"       # "ask" | "review"
    match_threshold: float = 0.72         # similitud mínima para reconocer un producto (0-1)


@dataclass
class Settings:
    telegram_token: str
    admin_chat_ids: set[int]
    # Base de datos SQLite (un archivo; se crea solo si no existe)
    database_path: str = "data/trazr.db"
    # Presentación
    currency_symbol: str = "$"
    timezone: str = "UTC"
    rules: Rules = field(default_factory=Rules)


def load_settings(require_telegram: bool = True, env_file: str | None = None) -> Settings:
    """Lee la configuración. Lanza ConfigError con un mensaje claro si falta algo."""
    load_dotenv(env_file or Path.cwd() / ".env", override=False)

    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if require_telegram and not token:
        raise ConfigError(
            "Falta TELEGRAM_BOT_TOKEN. Crealo con @BotFather y pegalo en el archivo .env."
        )

    admin_raw = os.getenv("ADMIN_CHAT_IDS", "").strip()
    admin_ids: set[int] = set()
    for part in admin_raw.replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            admin_ids.add(int(part))
        except ValueError as exc:
            raise ConfigError(f"ADMIN_CHAT_IDS contiene un valor no numérico: {part!r}") from exc

    on_unknown = os.getenv("ON_UNKNOWN_PRODUCT", "ask").strip().lower()
    if on_unknown not in {"ask", "review"}:
        raise ConfigError("ON_UNKNOWN_PRODUCT debe ser 'ask' o 'review'.")

    rules = Rules(
        require_price_valid=_bool(os.getenv("RULE_REQUIRE_PRICE_VALID"), True),
        require_stock=_bool(os.getenv("RULE_REQUIRE_STOCK"), True),
        max_order_total=_float("MAX_ORDER_TOTAL", 0.0),
        max_qty_per_item=_int("MAX_QTY_PER_ITEM", 1000),
        on_unknown_product=on_unknown,
        match_threshold=_float("MATCH_THRESHOLD", 0.72),
    )

    settings = Settings(
        telegram_token=token,
        admin_chat_ids=admin_ids,
        database_path=os.getenv("DATABASE_PATH", "data/trazr.db").strip() or "data/trazr.db",
        currency_symbol=os.getenv("CURRENCY_SYMBOL", "$").strip() or "$",
        timezone=os.getenv("TIMEZONE", "UTC").strip() or "UTC",
        rules=rules,
    )

    return settings
