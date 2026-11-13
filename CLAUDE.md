# Bot de pedidos (Trazr) — contexto

Bot de Telegram en Python que recibe pedidos de bebidas en lenguaje natural,
valida stock y precio contra la base SQLite, confirma o deriva a revisión del
admin. TFI de Administración de Recursos, UTN-FRT, comisión 4K1.

## Arquitectura
- `app/server.py` — `python -m app.server`: web + bot de Telegram en un proceso
- `app/web/` — web de admin: `main.py` (rutas FastAPI, login, CSRF), `templates/` (Jinja2), `static/` (CSS del diseño)
- `app/bot.py` — capa de Telegram, sin lógica de negocio
- `app/tunnel.py` — túnel de ngrok que abre el servidor si hay NGROK_DOMAIN
- `app/whatsapp.py` — canal WhatsApp (Cloud API): webhook `/whatsapp/webhook` (firma
  X-Hub-Signature-256, sin duplicados, procesa en segundo plano) y envío por la Graph API
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
- Los precios NO vencen (se sacó «vigente hasta» de todo).
- Clientes = empresas (pedidos grandes). Revisión del admin: el PRIMER pedido de cada cliente
  (REVISAR_CLIENTES_NUEVOS) hasta que tenga un pedido pagado; después todo se confirma solo.
  MAX_ORDER_TOTAL queda opcional (0 = apagado, por defecto). Producto sin precio → revisión.
- Los mensajes del chat NO se guardan en la base: la memoria de Gemini vive en RAM (12 h).
- Si Gemini no contesta: «dame un momento» (on_wait), reintentos (AI_RETRY_DELAYS) y si no,
  le pide al cliente que escriba en unos minutos + aviso en la web. Sin clave → reglas.
- Pago: el PRIMER pedido pregunta el medio tras confirmar (efectivo / transferencia); desde el
  segundo, el resumen trae el medio de siempre (`_usual_payment`, «como la última vez») con
  [✅ Confirmar] [💳 Cambiar pago] [❌ Cancelar] y confirmar deja el pedido hecho (se escribe
  «por transferencia» → confirma con ese medio) → pedido CONFIRMADO con
  pago «pendiente» (stock reservado) → el admin confirma el pago en la web → el bot avisa.
  Transferencia impaga se cancela sola a las PAGO_HORAS (estado CANCELADO, vuelve el stock).
  Pago = columnas pago_* en `pedidos`, aparte del estado. Mercado Pago (fase 6) llamará a
  `confirm_payment` igual que el botón de la web.
- Lo agotado no se nombra: catálogo, Gemini y el parser solo ven productos con stock (`_sellable`).
- Stock: se verifica al armar el borrador y otra vez al confirmar. Si no alcanza, el cliente
  NO puede confirmar: el bot le dice la cantidad exacta que queda y ofrece ajustar
  (🔧 / «sí»). Al modificar, lo que el pedido ya tenía cuenta como disponible.
  La revisión del admin queda para el primer pedido de cada cliente.
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
5. **WhatsApp Cloud API** (webhook en la misma app FastAPI) — EN CURSO (ver abajo)
6. Mercado Pago (link de pago + webhook idempotente)
7. Faltantes del TFI: crédito, recompra, planes. CRÉDITO (decidido, opción E): límite de deuda
   impaga por empresa; si un pedido lo supera, va a revisión o se pide pago anticipado.
8. (A evaluar al final) web para clientes. Por ahora el canal del cliente es el chat.

## Próxima sesión — empezar por acá

### Plan de mejora del bot (con el consultor) — propuesto, falta el OK del usuario
Página: https://claude.ai/artifact/1h6opp7ZJDfQngy1FAn6it. Hacerlo ANTES de Mercado Pago.
- Bugs confirmados en el simulador: esperando dirección, cualquier texto ≥8 letras se toma como
  dirección («esperá, agregale 2 fantas»); esperando pago, «no tengo plata, cancelá» elige efectivo
  y REGISTRA el pedido (answer_payment busca palabras sueltas, efectivo antes que cancelar).
- Núcleo nuevo: pedido en curso + operaciones (agregar/fijar/quitar/vaciar, unidad|bulto) que
  devuelve Gemini en un JSON por turno; pendientes (dudas) guardados en el Draft y resueltos con
  botones/listas; una sola voz (sistema) en pasos transaccionales.
- Etapas: 0 red de seguridad (bugs, sí/no sin Gemini, timeout, «escribiendo» WhatsApp, golden set
  ~50 casos + eventos) → 1 pedido en curso → 2 bultos (unidades_por_bulto) → 3 voz/forma, pago en
  el resumen, «lo de siempre» → Mercado Pago → 4 handoff a humano + métricas.
- ETAPA 0 HECHA: bugs de dirección (`_looks_like_address`: número o palabra de calle, y que no
  nombre productos ni empiece con «agregale/esperá…») y de pago (`_payment_choice`: un solo medio
  sin negar; cancelar primero; ante la duda vuelve a preguntar con botones); `_wants_confirm` /
  `_wants_cancel` sin Gemini; palabras clave (`_keyword`: catálogo, mis pedidos, ayuda,
  modificar, mi dirección; saludo sin Gemini → bienvenida); textos sin comandos con barra;
  cajas/packs → error "bulto" (parser.BULK_WORDS, también si Gemini pone número); Gemini 8 s y
  1 reintento; WhatsApp leído + «escribiendo»; «lo consulto con el equipo» → aviso "consulta";
  tabla `eventos` (sin texto) + `scripts/metricas.py`; set de prueba `tests/golden/casos.json`
  (56 casos) + `scripts/evaluar.py` (Gemini real, a mano; `--sin-ia`). Base con reglas: 42/56.
  Base con Gemini real (flash-lite): 53/56 (95 %), mediana ~1,5 s. Fallas: simple-09 («un par»
  → 1), bulto-04 (un renglón con duda tira todo el mensaje), cambio-04 («cambiá X por Y» no
  conserva la cantidad).
- ETAPA 1 ACHICADA — PROGRAMADA (OK del usuario: bloquear «sí» con dudas, «un par» = 2, postergar
  operaciones). `Draft.pending` + `_can_wait`/`_pending_from`/`resolve_pending`/`skip_pending`
  (botón sp:) /`_pending_question`; `confirm_draft` y `_place_order` no avanzan con pendientes;
  `_draft_for_ai` los pasa a Gemini. Solo en pedidos nuevos (no en modificaciones). Reglas: 50/62
  (46/56 originales). Con Gemini: simple 11/11, bulto 8/8, cambio 8/8 (centinela cambio-08 OK →
  operaciones siguen postergadas), duda 5/5. Falta correr el resto de los grupos para confirmar
  que no hubo regresión.
- ETAPA 2 (cajas) — PROGRAMADA. Decisión del usuario: a empresas se vende caja cerrada Y unidad
  suelta (25 con caja de 12 = 2 cajas + 1 suelta); cada presentación tiene su caja. Producto:
  `pack_size` (0 = sin caja cargada → el bot pregunta) y `pack_name` (caja/pack/fardo/cajón);
  columnas `unidades_bulto`/`nombre_bulto` (migración ALTER). Stock y precio siguen en UNIDADES.
  `parser.bulk_units` y en Gemini (`_ai_items_a_parsed`): las unidades las calcula el código.
  «sueltas» = unidades. Resumen, comprobante y web muestran «(2 cajas + 1 suelta)»
  (`Product.pack_label`). Web: Productos → unidades por caja y nombre; CSV: UnidadesPorCaja,
  NombreCaja. Catálogo de ejemplo con cajas (SPR1 sin caja, para probar la pregunta). Set: 65
  casos (bulto-01..11); reglas 53/65. Falta: correr bulto con Gemini; ¿la caja tiene otro precio?
  (hoy = unidades × precio unitario). DECIDIDO: la caja cuesta lo mismo que sus unidades.
- ETAPA 3 (parcial) — PAGO EN EL RESUMEN, programado: `Draft.payment` se precarga con el medio del
  último pedido; `confirm_draft` coloca el pedido si hay medio; botón cp: (`change_payment`);
  `_quick_answer` (sí/no/medio escrito/dirección suelta solo sin Gemini) antes de llamar a Gemini;
  palabras «cambiar dirección», «cambiar pago». Primer pedido y modificaciones: como antes. Set:
  68 casos (pago-05..07).
- ETAPA 3 COMPLETA: «lo de siempre» (`_wants_repeat` en service: frase corta sin cambios ni números →
  `repeat_last_order`, precios de hoy, mismo resumen; con cambios → Gemini con la lista completa);
  saludo (GREETINGS, con o sin Gemini, sin pisar un borrador) por nombre con botones
  [📋 Ver catálogo] (ct:) y, si ya compró, [🔁 Lo de siempre] (rp:); `ship_day()` «hoy / mañana /
  el lunes 16/11» por TURNOS (usuario: a la siesta no se reparte): confirmado antes de las 11
  (NEGOCIO_HORA_CORTE_MANANA) → hoy a la mañana (9 a 13); antes de las 19 (NEGOCIO_HORA_CORTE) →
  hoy a la tarde (17 a 21); si no, próximo día (NEGOCIO_DIAS_REPARTO lun-sab) a la mañana; en resumen y
  comprobante; plata sin «,00»; comprobante compacto (fecha «13/11 10:22», sin «Estado» si hay
  pago, «Total · envío incluido»). Horario nuevo (usuario): lunes a sábado 9–13 y 17–21
  (TRAZR, NEGOCIO_HORA_CIERRE=21). Set: 74 casos; reglas 61/74. Con Gemini: siempre 6/6.
  Catálogo: hasta 15 productos va entero; si no, primero categorías (botones cc:<categoría>, en
  WhatsApp lista «Ver categorías», `Outgoing.menu_label`), o escribiendo el nombre de la categoría.
  Un mensaje por paso: `_join` une la pregunta «¿qué dirección dejo guardada?» al comprobante.
  Arreglo: Gemini tomaba «3 fanta» después de un pedido como cambio al anterior → si no hay
  palabras de cambio (`CHANGE_CUES`), «cambio_pedido» sin borrador se trata como pedido nuevo.
  Detalle original: pendientes simples en el Draft
  (se arma con lo entendido, lo dudoso queda en Draft.pending; no se confirma con pendientes salvo
  «Seguir sin eso»; un número suelto completa el único pendiente), «un par» = 2 (parser + prompts),
  regla «cambiar X por Y conserva la cantidad» en CHAT_PROMPT. Operaciones (agregar/fijar/quitar)
  POSTERGADAS; se reabren si falla el caso centinela cambio-08 (pedido largo). Casos nuevos:
  bulto-06/07/08, simple-11, cambio-07, cambio-08. Listo = las 3 fallas + 6 nuevos pasan,
  ≥59/62 con Gemini sin regresión (2 de 3 corridas), reglas ≥42/56.
- Decisiones pendientes del usuario: cómo se venden los bultos; pago dentro del resumen; voz;
  funciones de WhatsApp (sí escribiendo/listas, no Flows/catálogo Meta); handoff; avisos con texto.

### Fase 5: WhatsApp — en curso
1. App de Meta creada (caso de uso WhatsApp) con número de prueba; el «Hello World» llegó. HECHO.
2. Canal programado: `app/whatsapp.py`, `WhatsAppConfig` en config (WHATSAPP_TOKEN,
   WHATSAPP_PHONE_NUMBER_ID, WHATSAPP_APP_SECRET, WHATSAPP_VERIFY_TOKEN), registrado en el
   Notifier y en `server.py`; tests en `tests/test_whatsapp.py`. HECHO.
   Detalles: HTML del bot → *negrita* _cursiva_ `código`; botones → reply buttons (≤3, títulos
   ≤20; más de 3 → lista); resumen >1024 caracteres va aparte de los botones; Argentina: si Meta
   rechaza 549… (131030) se reintenta sin el 9 y se recuerda.
3. Túnel: ngrok con dominio fijo gratis. Si NGROK_DOMAIN está en el .env, `python -m app.server`
   lo abre solo (`app/tunnel.py`, subproceso; Ctrl+C apaga todo). El usuario prefiere UN comando
   en la terminal de VS Code (se descartó iniciar.bat/.ps1). Antes: cloudflared quick tunnel (la URL cambiaba al reiniciarlo) + webhook en Meta (campo
   «messages») + suscripción de la app a la WABA (POST /<WABA_ID>/subscribed_apps). HECHO:
   el bot contesta por WhatsApp. Flujo completo probado por el usuario (pedido → aprobar en la
   web → aviso → pago recibido → aviso): FUNCIONA.
4. Plantillas para avisos fuera de la ventana de 24 h — PROGRAMADO: `Outgoing.template`
   (clave + valores) en approve/reject/confirm_payment/cancel_unpaid; `PLANTILLAS` en whatsapp.py
   (utilidad, es_AR). Si no hubo mensaje del cliente en 23,5 h (memoria) → plantilla directa;
   si no, mensaje normal y, ante 131047 (en la respuesta o en un estado «failed» del webhook),
   plantilla. Se crean con `python -m scripts.plantillas_whatsapp --crear` (WHATSAPP_WABA_ID).
   Creadas por el usuario: en espera de aprobación de Meta.
   Política de privacidad pública en /privacidad (#borrar = instrucciones de borrado); NEGOCIO_CONTACTO.
5. Pendiente (producción): token permanente (usuario del sistema), número propio,
   verificación del negocio, servidor con HTTPS.

### Fase 4: web de administración — HECHA (usuarios reales creados por el usuario)
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
Usuarios reales creados en la PC del usuario: fase 4 cerrada.

Ajustes posteriores (hechos): web que se refresca sola (/cambios), menú hamburguesa y tablas
como tarjetas en el celular, tablero propio de Trazr (remito del día, el día en reparto con el
corte de las 13, cola de decisiones, se está por acabar).

Método de diseño: partir de buenas referencias, skills de diseño y diseño real antes de programar.
- Claude Design (artifacts «Design System» y «Design»); Figma como alternativa (el MCP de Figma
  hoy no conecta: la red del entorno lo bloquea).
- Skills: `frontend-design`; `web-animation-design` y `emil-design-engineering` (audit-suite);
  `css-animations`/`ui-motion` (hzblj-skills). GSAP no hace falta para esta web.
