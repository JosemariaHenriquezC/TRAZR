# Bot de pedidos (Trazr) — contexto

Bot de Telegram en Python que recibe pedidos de bebidas en lenguaje natural,
valida stock y precio contra la base SQLite, confirma o deriva a revisión del
admin. TFI de Administración de Recursos, UTN-FRT, comisión 4K1.

## Arquitectura
- `app/bot.py` — capa de Telegram, sin lógica de negocio
- `app/service.py` — `BotService`: lógica central, independiente del canal
- `app/parser.py` — intérprete por reglas (respaldo)
- `app/ai_parser.py` — intérprete con Gemini; misma firma que `parse_order`
- `app/rules.py` — regla "dentro de los parámetros"
- `app/storage/` — interfaz `Repository` (`base.py`) e implementación SQLite (`sqlite.py`)
- `scripts/init_db.py` — crea la base y carga el catálogo (`--ejemplo` o `--csv`)

## Reglas del proyecto
- Catálogo solo de bebidas.
- La IA solo interpreta texto. Precios, stock y estados salen de la base y de rules.py.
- `service.py` no debe depender de Telegram: va a sumarse WhatsApp. Los clientes se
  identifican por `canal` + `externo_id`.
- Ningún pedido se registra sin dirección de envío. El envío siempre está incluido, sin costo.
- Nunca commitear `.env`, `credentials.json` ni la base (`data/`).
- Los tests no deben llamar a APIs reales. Correr con `python -m pytest -q`.

## Plan por fases
0. Preparación — hecha
1. Gemini como intérprete — hecha (falta que `service.py` importe `ai_parser.parse_order`)
2. SQLite + dirección obligatoria (envío siempre incluido, sin costo) — hecha
3. Flujo de dirección en el bot (botones Sí/Cambiar)
4. FastAPI + webhook de WhatsApp Cloud API
5. Mercado Pago (link de pago + webhook idempotente)
6. Faltantes del TFI: crédito, panel, recompra, planes
