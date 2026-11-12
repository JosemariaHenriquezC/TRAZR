# Trazr — bot de pedidos de bebidas para Telegram

Bot de Telegram que recibe pedidos de bebidas en lenguaje natural, consulta stock
y precio vigente en tiempo real, confirma solo lo que está dentro de los
parámetros, deriva el resto al administrador y registra todo en una base de datos
SQLite. El envío está **siempre incluido, sin costo**, y cada pedido lleva la
dirección de entrega del cliente.

TFI de Administración de Recursos — UTN-FRT, comisión 4K1.

---

## 1. Decisiones técnicas

| Elección | Por qué |
|---|---|
| **Python 3.10+** | Ecosistema maduro para bots; fácil de leer y modificar. |
| **python-telegram-bot 21** | Librería recomendada por Telegram, asíncrona, con botones inline. Usa *long polling*: no hace falta dominio ni abrir puertos. |
| **SQLite** | Toda la información (catálogo, clientes, pedidos) en un solo archivo, sin servidor ni credenciales. Transacciones reales: un pedido se registra completo o no se registra. La propia base impide stock negativo y pedidos sin dirección. Viene incluida en Python. |
| **Gemini conversacional** | Entiende jerga y el contexto de la charla («agregale una más», «con gas»), distingue pedidos, consultas y pedidos de cambio, y redacta las respuestas con tono humano. **Límite:** precios, stock, totales y estados los pone el bot desde la base; si Gemini escribe un precio o da un pedido por confirmado, su texto se descarta. Si Gemini falla, el bot sigue con el parser por reglas y sus mensajes de siempre. Necesita `GEMINI_API_KEY`; al arrancar, el bot avisa si quedó apagado. |
| **Parser por reglas** (respaldo) | Funciona sin claves ni costo. Tolera tildes, plurales, errores de tipeo, «x 3», «media docena», números en palabra. |

## 2. Regla «dentro de los parámetros»

**Stock:** se verifica **antes** de armar el pedido. Si algo no alcanza, el cliente **no puede
confirmarlo**: el bot le dice cuánto queda de cada producto («Cerveza Rubia 1L: pediste 10, me
quedan 5») y le ofrece **🔧 Ajustar a lo que hay**, o mandar otra lista. Un producto sin stock
no se puede pedir. Al confirmar se vuelve a mirar (otro cliente pudo llevárselo).

Con stock suficiente, un pedido se **confirma automáticamente** si además:

1. **Producto existente**: está en el catálogo y activo.
2. **Precio vigente**: `precio` > 0 y `vigente_hasta` vacío o igual/posterior a hoy.
3. *(Opcional)* el total no supera `MAX_ORDER_TOTAL`.

| Situación | Qué pasa |
|---|---|
| Cumple todo | **CONFIRMADO**, se descuenta el stock, el cliente recibe el detalle. |
| No alcanza el stock | No se puede confirmar: el bot dice cuánto queda y ofrece ajustar. No se registra nada. |
| Precio vencido o supera el total máximo | **EN REVISION**, no se toca el stock, el admin recibe el pedido con botones Aprobar / Rechazar. |
| Producto inexistente | Por defecto se le pide al cliente que corrija (no se registra). Con `ON_UNKNOWN_PRODUCT=review` se registra en revisión. |
| Mensaje ambiguo, sin cantidad, cantidad inválida | Se pide aclaración. No se registra nada. |
| El cliente no tiene dirección guardada | El pedido queda **retenido** y se le pide la dirección (ver §3). |
| Falla de la base de datos | Mensaje claro al cliente, alerta al admin con el error. No queda nada a medias. |

Todo se ajusta en el `.env` (`RULE_REQUIRE_PRICE_VALID`, `RULE_REQUIRE_STOCK`,
`MAX_ORDER_TOTAL`, `MAX_QTY_PER_ITEM`, `ON_UNKNOWN_PRODUCT`, `MATCH_THRESHOLD`).

**Al aprobar** un pedido en revisión, el bot vuelve a verificar stock y precio
**en ese momento**. Si todavía falta stock o el precio sigue vencido, te dice qué
comando usar (`/sumar`, `/precio`) y no aprueba.

## 3. Confirmación, dirección y modificación de pedidos

**Nada se registra sin que el cliente lo confirme.** El flujo (`app/checkout.py`):

1. El cliente escribe su pedido. El bot arma un **borrador**: no registra nada ni toca el stock.
   Si no alcanza el stock, se lo dice con la cantidad que queda y le ofrece ajustar (ver §2).
2. Si el cliente no tiene dirección guardada, se la pide. Ningún pedido se registra sin
   dirección (lo valida el bot **y** la base de datos).
3. Le muestra un **resumen** con los precios vigentes, el total, el envío (incluido, sin
   costo) y la dirección guardada. Si el pedido va a quedar en revisión (precio vencido o
   total máximo), se lo avisa antes. Debajo, tres botones:
   **✅ Confirmar** · **📍 Cambiar dirección** · **❌ Cancelar**.
4. Antes de confirmar puede cambiar lo que quiera: mandar la lista nueva («mejor 3 cocas y
   una sprite»), otra dirección, o cancelar. Cada cambio genera un resumen nuevo (los botones
   de resúmenes viejos dejan de funcionar).
5. Recién al confirmar (botón, «sí» o `/confirmar`) se evalúa la regla con el stock y precio
   de ese momento y se registra **CONFIRMADO** o **EN REVISION**.

**Modificar un pedido ya hecho** (productos y/o dirección): durante `MODIFICACION_MINUTOS`
(60 por defecto) desde que se hizo, si no fue rechazado. Con `/modificar`, o diciéndolo
(«cambiame la fanta por una sprite», «mandalo a otra dirección»). El bot arma un resumen de
cómo quedaría y pide confirmación. Al confirmar devuelve el stock del pedido viejo, evalúa el
nuevo y descuenta lo que corresponde; el pedido conserva su ID. Si el cambio lo deja fuera de
los parámetros, vuelve a revisión y el admin recibe los botones. Fuera de término, el bot le
avisa al admin para que lo resuelva a mano.

**Dirección guardada:** la primera que da el cliente se guarda sola. Si después un pedido (o
una modificación) va a otra dirección, la guardada **no** cambia: una vez registrado el pedido,
el bot pregunta cuál dejar para la próxima, con los botones **🏠 La de siempre** · **📍 La nueva**
(también se puede contestar escribiendo «la nueva» o «la de siempre»; si el cliente no contesta,
queda la de siempre). `/direccion nueva dirección` la cambia directamente.

Otros detalles: cada pedido guarda su **copia** de la dirección; un borrador sin confirmar
vence a las 24 h.

## 4. Cómo conversa el bot (Gemini)

Con cada mensaje, Gemini recibe: el catálogo (código, nombre, alias, categoría y si hay o
no stock, **sin precios**), los datos del negocio del `.env` (`NEGOCIO_*`), los últimos
pedidos del cliente y la conversación de las últimas 12 horas (tabla `mensajes`). Devuelve
la intención y un texto corto:

| Intención | Qué hace el bot |
|---|---|
| pedido | Valida los productos y cantidades y sigue el flujo normal; el texto de Gemini va arriba del detalle. |
| consulta | Muestra el texto de Gemini + precio y disponibilidad **tomados de la base**. |
| cambio_pedido | Responde y le avisa al admin con el mensaje y el último pedido del cliente. |
| charla | Responde el texto de Gemini (saludos, preguntas para aclarar, horarios, etc.). |

## 5. Base de datos

Un archivo SQLite (`DATABASE_PATH`, por defecto `data/trazr.db`) que el bot crea
solo. Para verlo o editarlo a mano: [DB Browser for SQLite](https://sqlitebrowser.org/).

| Tabla | Contenido |
|---|---|
| `productos` | `sku` (PK), `producto`, `categoria` (Gaseosas, Aguas, Cervezas, Vinos, Aperitivos, Jugos, Energizantes), `alias` (separados por coma), `stock` (≥ 0), `precio`, `vigente_hasta` (AAAA-MM-DD o vacío), `activo`, `actualizado` |
| `clientes` | `canal` + `externo_id` (únicos; ej. `telegram` + ID del chat), `nombre`, `direccion`, `creado`, `actualizado` |
| `pedidos` | `pedido_id` (PK, ej. `P261112-A3F1`), `fecha`, `canal`, `cliente_id`, `cliente_nombre`, `mensaje_original`, `direccion_envio` (obligatoria), `costo_envio`, `total`, `estado` (`CONFIRMADO` / `EN REVISION` / `RECHAZADO`), `motivo`, `actualizado`, `revisado_por` |
| `pedido_items` | renglones de cada pedido: `sku`, `producto`, `cantidad`, `precio_unitario`, `subtotal` |
| `pedidos_pendientes` | pedido retenido (ya interpretado) esperando la dirección del cliente |
| `mensajes` | historial reciente de cada chat (memoria de Gemini; se guardan los últimos 50) |

### Cargar el catálogo

```bash
python -m scripts.init_db --ejemplo           # 10 bebidas de ejemplo (si el catálogo está vacío)
python -m scripts.init_db --csv catalogo.csv  # alta/actualización desde un CSV
```

El CSV lleva el encabezado `SKU,Producto,Categoria,Alias,Stock,Precio,Vigente_hasta,Activo`
(coma o punto y coma; se puede exportar desde Excel). Si un SKU ya existe, se
actualiza. Nunca se borra nada. Los ejemplos incluyen uno con precio vencido
(`FER1`) y uno sin stock (`VIN1`) para probar las revisiones.

## 6. Puesta en marcha

### Paso 0: probar sin Telegram

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # Windows: copy .env.example .env
python -m scripts.init_db --ejemplo
python -m scripts.simulate         # chateás con el bot en la consola
python -m pytest -q                # corre todos los tests
```

### Paso 1: crear el bot en Telegram

1. En Telegram abrí **@BotFather** → `/newbot`.
2. Elegí un nombre visible y un usuario terminado en `bot`.
3. BotFather te da un **token**. Pegalo en `.env` como `TELEGRAM_BOT_TOKEN=...`.
   Es secreto: si se filtra, en BotFather usá `/revoke`.

### Paso 2: ejecutar y hacerte administrador

```bash
python -m app.bot
```

1. En Telegram buscá tu bot y mandale `/miid`. Te responde tu ID.
2. Ponelo en `.env`: `ADMIN_CHAT_IDS=123456789` (varios admins: separados por coma).
3. Cortá el bot (Ctrl+C) y volvé a ejecutarlo. Ya sos admin: `/ayuda` muestra los comandos de gestión.

El bot funciona **mientras el proceso esté corriendo**. Para 24/7 necesitás una PC
siempre prendida o un servidor. Hacé copia del archivo `data/trazr.db` con regularidad.

## 7. Comandos

**Clientes:** escribir el pedido en texto libre · `/catalogo` · `/confirmar` · `/cancelar` ·
`/modificar` · `/direccion [nueva]` · `/mispedidos` · `/miid` · `/ayuda`

**Administrador:**

| Comando | Qué hace |
|---|---|
| `/stock [texto o SKU]` | Consulta stock, precio y vigencia (todo o filtrado) |
| `/setstock SKU cantidad` | Fija el stock |
| `/sumar SKU cantidad` | Suma (o resta con negativo) al stock; no permite negativos |
| `/precio SKU valor [AAAA-MM-DD]` | Precio y vigencia (sin fecha = sin vencimiento) |
| `/revision` | Lista pedidos en revisión con botones |
| `/pedido ID` | Ver un pedido |
| `/aprobar ID` o botón ✅ | Aprueba: verifica, descuenta stock y avisa al cliente |
| `/rechazar ID [motivo]` o botón ❌ | Rechaza y avisa al cliente con el motivo |

## 8. Prueba de punta a punta

Con las bebidas de ejemplo. Todo está automatizado en `tests/` (`python -m pytest -q`).

| # | Quién | Mensaje | Resultado esperado |
|---|---|---|---|
| 1 | Cliente nuevo | `2 coca y 1 sprite` | 📍 *¿A qué dirección te lo enviamos? El envío está incluido, sin costo.* No se registra nada. |
| 1b | Cliente nuevo | `Av. Sarmiento 450, Yerba Buena` | 🧾 *Revisá tu pedido* con precios, envío incluido, la dirección y Total $ 9.400,00, y los botones ✅ 📍 ❌. Todavía no se registra. |
| 1c | Cliente nuevo | ✅ Confirmar (o `sí`) | ✅ *¡Pedido confirmado!* COC1 50→48. |
| 2 | Cliente | `2 coca` → 📍 Cambiar dirección → `Belgrano 300, Tafí Viejo` → `sí` | El resumen se actualiza con la dirección nueva y el pedido sale a esa dirección. Después: *¿Qué dirección dejo guardada para tus próximos pedidos?* [🏠 La de siempre] [📍 La nueva]. |
| 2b | Cliente | `/modificar` → `3 coca, 1 sprite` → `sí` | ✏️ *¡Pedido modificado y confirmado!* Mismo ID, stock reajustado. El admin recibe el aviso. |
| 3 | Cliente | `mandame 10 cervezas` | ⚠️ *No me alcanza el stock: Cerveza Rubia 1L: pediste 10, me quedan 5.* Botones 🔧 Ajustar / ❌ Cancelar, sin Confirmar. No se registra nada. |
| 3b | Cliente | `sí` (o 🔧) y después `sí` | Ajusta a 5, muestra el resumen y confirma. CER1 5→0. |
| 3c | Cliente | `2 vino` | *Vino Tinto Malbec 750ml: pediste 2, no me queda.* No se puede pedir. |
| 4 | Cliente | `1 fernet` → `sí` | En revisión por `Precio no vigente: Fernet 750ml`. Admin: `/precio FER1 13000` y `/aprobar ID`. Si se rechaza: `/rechazar ID motivo` → al cliente le llega ❌ con el motivo. |
| 6 | Cliente | `3 agua` | *¿cuál de estos? Agua Mineral con Gas 2L, Agua Mineral sin Gas 2L*. No se registra. |
| 7 | Cliente | `2 whisky` | *«whisky»: no está en nuestro catálogo*. |
| 8 | Cliente | `2,5 coca` / `-3 sprite` | *la cantidad debe ser un número entero entre 1 y 1000*. |
| 9 | Cliente | `/setstock COC1 0` | ⛔ *Ese comando es solo para el administrador.* |

## 9. Estructura del código

```
app/
  bot.py          capa de Telegram (handlers, botones, arranque); sin lógica de negocio
  service.py      BotService: entrada de mensajes, comandos y revisión del admin (independiente del canal)
  checkout.py     borrador, resumen, confirmación, dirección y modificación de pedidos
  messages.py     mensaje de salida genérico (texto + botones)
  ai_parser.py    Gemini: intérprete (parse_order) y conversación (conversar)
  parser.py       intérprete por reglas (respaldo)
  rules.py        regla «dentro de los parámetros»
  config.py       variables de entorno y reglas configurables
  models.py       estructuras de datos (Product, Customer, Order, ...)
  sample_data.py  bebidas de ejemplo
  storage/
    base.py       interfaz Repository
    sqlite.py     base de datos SQLite (esquema y consultas)
scripts/
  init_db.py      crea la base y carga el catálogo (ejemplo o CSV)
  simulate.py     chat por consola sin Telegram
  probar_ia.py    prueba el intérprete contra Gemini real
tests/            pruebas de punta a punta, dirección, base de datos e IA (sin APIs reales)
```

## 10. Suposiciones

1. El catálogo es **solo de bebidas**.
2. El envío está siempre incluido en el precio: no se cobra aparte.
3. Un pedido en revisión **no reserva stock**; se descuenta recién al aprobar (y al aprobar
   se vuelve a verificar).
4. El bot le muestra al cliente la cantidad exacta que queda de un producto cuando no le alcanza.
5. Al aprobar se usa el precio vigente en ese momento.
6. Cualquiera que escriba al bot puede pedir. Solo los IDs de `ADMIN_CHAT_IDS` gestionan.
7. El bot corre como **un solo proceso**; un candado interno evita descontar dos veces el mismo stock.
8. Los clientes se identifican por canal + ID, así el mismo número en Telegram y WhatsApp (fase 5) no se mezcla.
9. Moneda: solo el símbolo es configurable (`CURRENCY_SYMBOL`); los importes se muestran con formato `1.234,50`.
