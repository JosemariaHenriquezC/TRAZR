# Bot de pedidos (Trazr) — contexto

Bot de Telegram en Python que recibe pedidos de bebidas en lenguaje natural,
valida stock y precio contra la base SQLite, confirma o deriva a revisión del
admin. TFI de Administración de Recursos, UTN-FRT, comisión 4K1.

## Arquitectura
- `app/server.py` — `python -m app.server`: web + bot de Telegram en un proceso
- `app/web/` — web de admin: `main.py` (rutas FastAPI, login, CSRF), `templates/` (Jinja2), `static/` (CSS del diseño)
- `app/bot.py` — capa de Telegram, sin lógica de negocio
- `app/service.py` — `BotService`: entrada de mensajes, comandos, revisión del admin (independiente del canal)
- `app/checkout.py` — borrador → resumen → confirmación; dirección; modificar pedidos hechos
- `app/messages.py` — `Outgoing` (texto + botones + canal)
- `app/admin.py` — acciones del admin para la web (`AdminResult`: ok, mensaje, `notify` para clientes)
- `app/auth.py` — hash de contraseñas (scrypt) · `app/notifier.py` — envío por canal del cliente
- `app/parser.py` — intérprete por reglas (respaldo)
- `app/ai_parser.py` — Gemini: `parse_order` (intérprete) y `conversar` (intención + respuesta humana)
- `app/rules.py` — regla "dentro de los parámetros"
- `app/storage/` — interfaz `Repository` (`base.py`) e implementación SQLite (`sqlite.py`)
- `scripts/init_db.py` — crea la base y carga el catálogo (`--ejemplo` o `--csv`)

## Reglas del proyecto
- Catálogo solo de bebidas.
- Gemini interpreta y redacta, pero NUNCA pone números: precios, stock, totales y estados
  salen de la base y de rules.py (`respuesta_segura` descarta textos con plata o "confirmado").
- `service.py` no debe depender de Telegram: va a sumarse WhatsApp. Los clientes se
  identifican por `canal` + `externo_id`.
- Los precios NO vencen (se sacó «vigente hasta» de todo). Revisión del admin solo si el total
  supera MAX_ORDER_TOTAL (150000 por defecto) o un producto no tiene precio.
- Los mensajes del chat NO se guardan en la base: la memoria de Gemini vive en RAM (12 h).
- Si Gemini no contesta: «dame un momento» (on_wait), reintentos (AI_RETRY_DELAYS) y si no,
  le pide al cliente que escriba en unos minutos + aviso en la web. Sin clave → reglas.
- Pago: tras confirmar, el bot pregunta el medio (efectivo / transferencia) → pedido CONFIRMADO con
  pago «pendiente» (stock reservado) → el admin confirma el pago en la web → el bot avisa.
  Transferencia impaga se cancela sola a las PAGO_HORAS (estado CANCELADO, vuelve el stock).
  Pago = columnas pago_* en `pedidos`, aparte del estado. Mercado Pago (fase 6) llamará a
  `confirm_payment` igual que el botón de la web.
- Lo agotado no se nombra: catálogo, Gemini y el parser solo ven productos con stock (`_sellable`).
- Stock: se verifica al armar el borrador y otra vez al confirmar. Si no alcanza, el cliente
  NO puede confirmar: el bot le dice la cantidad exacta que queda y ofrece ajustar
  (🔧 / «sí»). Al modificar, lo que el pedido ya tenía cuenta como disponible.
  La revisión del admin queda solo para el total máximo.
- Ningún pedido se registra sin dirección de envío ni sin confirmación del cliente.
  El envío siempre está incluido, sin costo.
- La dirección guardada solo cambia si el cliente lo elige: tras un pedido a otra dirección,
  el bot pregunta cuál dejar (🏠 la de siempre / 📍 la nueva). La primera se guarda sola.
- Tienda ficticia de ejemplo: «Trazr» (valores por defecto en `config.TRAZR`).
- Nunca commitear `.env`, `credentials.json` ni la base (`data/`).
- Los tests no deben llamar a APIs reales (`Settings.ai` viene apagado por defecto;
  para probar con IA se pasa un cliente simulado a `BotService(ai_client=...)`). Correr con `python -m pytest -q`.

## Herramientas del proyecto
- Agente `consultor` (`.claude/agents/consultor.md`, modelo Fable, solo lectura): consultarlo
  antes de decisiones de arquitectura, diseño o alcance. Compara opciones y recomienda una.
- Plugins para la fase 4: `frontend-design`, `audit-suite` (skills de Emil Kowalski:
  `web-animation-design`, `emil-design-engineering`), `figma`, `playwright` (navegador para
  probar la web y sacar capturas).

## Plan por fases
0. Preparación — hecha
1. Gemini como intérprete — hecha y conectada en `service.py` (respaldo: reglas)
2. SQLite + dirección obligatoria (envío siempre incluido, sin costo) — hecha
3. Confirmación del pedido y de la dirección (botones), modificación de pedidos hechos — hecha
4. **Web de administración** (FastAPI) — SOLO para el admin
5. WhatsApp Cloud API (webhook en la misma app FastAPI)
6. Mercado Pago (link de pago + webhook idempotente)
7. Faltantes del TFI: crédito, recompra, planes
8. (A evaluar al final) web para clientes. Por ahora el canal del cliente es el chat.

## Próxima sesión — empezar por acá

### Fase 4: web de administración — HECHA (falta crear los usuarios reales)
Decisiones tomadas (usuario + consultor):
- **Revisión del admin solo en la web.** El chat es solo para clientes: se QUITAN los comandos
  de admin del chat (/stock, /setstock, /sumar, /precio, /revision, /pedido, /aprobar,
  /rechazar) y los botones `ap:`/`rj:`. El admin no recibe nada por Telegram.
- **Avisos al admin solo en la web:** lo que hoy manda `_admins()` (pedido en revisión, pedido
  modificado, cambio fuera de término, falla de base) se guarda en una tabla `avisos` y se ve
  en la web (contador + lista), no por chat.
- **Varios admins:** tabla `admins` en SQLite (usuario, nombre, hash scrypt de la contraseña
  con `hashlib`, activo). Script para crear admins. `Revisado_por` = admin logueado.
- **Pantallas:** pedidos (filtro por estado) + detalle con aprobar/rechazar con motivo;
  stock, precios y productos (alta/edición, reemplaza al CSV); clientes (dirección y pedidos);
  tablero (ventas del día/semana, en revisión, poco stock, avisos).
- **Un solo proceso:** FastAPI con el bot de Telegram dentro del lifespan
  (`initialize/start/updater.start_polling`), un worker, sin --reload. Un comando levanta todo.
- **Reutilizar lógica sin duplicar:** `approve/reject` devuelven un resultado (ok, mensaje,
  pedido, avisos para el cliente); `set_stock/add_stock/set_price` reciben valores tipados;
  `Outgoing` suma `channel`; un `Notifier` envía al canal del cliente (Telegram ahora,
  WhatsApp en la fase 5). Validaciones de alta de productos en `BotService`, no en las rutas.
- **Frontend:** FastAPI + Jinja2 server-rendered, formularios clásicos (POST + redirect) y CSS
  propio; animaciones sutiles con CSS (transiciones, View Transitions). HTMX, si hace falta,
  en una segunda pasada y puntual.
- **Seguridad:** sesión con `SessionMiddleware` (cookie firmada, httponly, samesite=lax),
  `SECRET_KEY` en `.env`, token CSRF en formularios. Dependencias nuevas: fastapi, uvicorn,
  jinja2, python-multipart, itsdangerous.
- **Tests:** `TestClient` con base temporal y notificador simulado. Playwright solo para
  capturas/demo, no en la suite.

Diseño:
- Sistema de diseño «Trazr Admin» (Claude Design): https://claude.ai/artifact/DLtCSwjvKvMqwU2A3r53Qt
  Verde `monte` (marca/acción) + amarillo `limon` (identidad, solo como fondo), estados
  ok/warn/danger, Archivo (títulos) + Inter (texto) + IBM Plex Mono (IDs/SKU). Componentes
  como clases CSS `tz-*` (Button, StatusBadge, OrdersTable, StatTile, Field, Notice): su
  `bundle.css` se copia a la web real. Aprobado por el usuario.
- Pantallas (canvas de Claude Design): https://claude.ai/artifact/TxLisYokWRD85qC63RVNH9
  Tablero, Pedidos (filtro por estado), Detalle de pedido (aprobar con precio nuevo / rechazar
  con motivo, historial), Productos y stock (sumar stock en la fila, editar producto),
  Clientes (con detalle y pedidos) e Ingreso. Su `admin.css` (variables + estructura de página)
  es la base del CSS de la web real. Aprobado por el usuario.

Orden de trabajo:
1. Diseño en Claude Design (Design System de Trazr + pantallas) → aprobado. HECHO.
2. Backend: quitar admin del chat, avisos y admins en la base, resultados tipados, Notifier. HECHO
   (admin.py, auth.py, notifier.py, scripts/crear_admin.py; ADMIN_CHAT_IDS eliminado).
3. Web: login, pantallas según el diseño aprobado, tests. HECHO (tests/test_web.py).
4. Capturas con Playwright para revisar. HECHO (para el TFI se pueden repetir a escala 2).
Pendiente de la fase 4: crear los usuarios reales en la PC del usuario (scripts/crear_admin.py).

Ajustes posteriores (hechos): web que se refresca sola (/cambios), menú hamburguesa y tablas
como tarjetas en el celular, tablero propio de Trazr (remito del día, el día en reparto con el
corte de las 13, cola de decisiones, se está por acabar).

Método de diseño: partir de buenas referencias, skills de diseño y diseño real antes de programar.
- Claude Design (artifacts «Design System» y «Design»); Figma como alternativa (el MCP de Figma
  hoy no conecta: la red del entorno lo bloquea).
- Skills: `frontend-design`; `web-animation-design` y `emil-design-engineering` (audit-suite);
  `css-animations`/`ui-motion` (hzblj-skills). GSAP no hace falta para esta web.
