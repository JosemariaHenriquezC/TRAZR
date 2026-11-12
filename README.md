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
| **Gemini como intérprete** (fase 1) | Entiende jerga y abreviaturas. Solo interpreta texto: precios, stock y estados salen de la base y de `rules.py`. Si falla, interpreta el parser por reglas. El módulo está en `ai_parser.py`; **`service.py` todavía usa `parser.parse_order`** (falta cambiar el import). |
| **Parser por reglas** (respaldo) | Funciona sin claves ni costo. Tolera tildes, plurales, errores de tipeo, «x 3», «media docena», números en palabra. |

## 2. Regla «dentro de los parámetros»

Un pedido se **confirma automáticamente** solo si **todos** sus renglones cumplen:

1. **Producto existente**: está en el catálogo y activo.
2. **Precio vigente**: `precio` > 0 y `vigente_hasta` vacío o igual/posterior a hoy.
3. **Stock suficiente**: `stock` ≥ cantidad pedida.
4. *(Opcional)* el total no supera `MAX_ORDER_TOTAL`.

| Situación | Qué pasa |
|---|---|
| Cumple todo | **CONFIRMADO**, se descuenta el stock, el cliente recibe el detalle. |
| Falta stock, precio vencido o supera el total máximo | **EN REVISION**, no se toca el stock, el admin recibe el pedido con botones Aprobar / Rechazar. |
| Producto inexistente | Por defecto se le pide al cliente que corrija (no se registra). Con `ON_UNKNOWN_PRODUCT=review` se registra en revisión. |
| Mensaje ambiguo, sin cantidad, cantidad inválida | Se pide aclaración. No se registra nada. |
| El cliente no tiene dirección guardada | El pedido queda **retenido** y se le pide la dirección (ver §3). |
| Falla de la base de datos | Mensaje claro al cliente, alerta al admin con el error. No queda nada a medias. |

Todo se ajusta en el `.env` (`RULE_REQUIRE_PRICE_VALID`, `RULE_REQUIRE_STOCK`,
`MAX_ORDER_TOTAL`, `MAX_QTY_PER_ITEM`, `ON_UNKNOWN_PRODUCT`, `MATCH_THRESHOLD`).

**Al aprobar** un pedido en revisión, el bot vuelve a verificar stock y precio
**en ese momento**. Si todavía falta stock o el precio sigue vencido, te dice qué
comando usar (`/sumar`, `/precio`) y no aprueba.

## 3. Dirección obligatoria y envío sin costo

- Ningún pedido se registra sin dirección de envío (lo valida el bot **y** la base de datos).
- La primera vez que un cliente pide, el bot interpreta el pedido, lo **retiene** y le pregunta
  la dirección. Con la respuesta guarda la dirección en su ficha de cliente y procesa el
  pedido retenido (con el stock y precio de ese momento).
- Los pedidos siguientes usan la dirección guardada. Cada pedido guarda una **copia** de la
  dirección, así un cambio posterior no altera pedidos viejos.
- `/direccion` muestra la dirección; `/direccion nueva dirección` la cambia;
  `/cancelar` descarta un pedido retenido. Un pedido retenido vence a las 24 h.
- El envío figura en cada pedido como «incluido, sin costo» (`costo_envio = 0`).

> La confirmación de dirección con botones (Sí / Cambiar) es la fase 3.

## 4. Base de datos

Un archivo SQLite (`DATABASE_PATH`, por defecto `data/trazr.db`) que el bot crea
solo. Para verlo o editarlo a mano: [DB Browser for SQLite](https://sqlitebrowser.org/).

| Tabla | Contenido |
|---|---|
| `productos` | `sku` (PK), `producto`, `categoria` (Gaseosas, Aguas, Cervezas, Vinos, Aperitivos, Jugos, Energizantes), `alias` (separados por coma), `stock` (≥ 0), `precio`, `vigente_hasta` (AAAA-MM-DD o vacío), `activo`, `actualizado` |
| `clientes` | `canal` + `externo_id` (únicos; ej. `telegram` + ID del chat), `nombre`, `direccion`, `creado`, `actualizado` |
| `pedidos` | `pedido_id` (PK, ej. `P261112-A3F1`), `fecha`, `canal`, `cliente_id`, `cliente_nombre`, `mensaje_original`, `direccion_envio` (obligatoria), `costo_envio`, `total`, `estado` (`CONFIRMADO` / `EN REVISION` / `RECHAZADO`), `motivo`, `actualizado`, `revisado_por` |
| `pedido_items` | renglones de cada pedido: `sku`, `producto`, `cantidad`, `precio_unitario`, `subtotal` |
| `pedidos_pendientes` | pedido retenido esperando la dirección del cliente (uno por cliente) |

### Cargar el catálogo

```bash
python -m scripts.init_db --ejemplo           # 10 bebidas de ejemplo (si el catálogo está vacío)
python -m scripts.init_db --csv catalogo.csv  # alta/actualización desde un CSV
```

El CSV lleva el encabezado `SKU,Producto,Categoria,Alias,Stock,Precio,Vigente_hasta,Activo`
(coma o punto y coma; se puede exportar desde Excel). Si un SKU ya existe, se
actualiza. Nunca se borra nada. Los ejemplos incluyen uno con precio vencido
(`FER1`) y uno sin stock (`VIN1`) para probar las revisiones.

## 5. Puesta en marcha

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

## 6. Comandos

**Clientes:** escribir el pedido en texto libre · `/catalogo` · `/direccion [nueva]` ·
`/cancelar` · `/mispedidos` · `/miid` · `/ayuda`

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

## 7. Prueba de punta a punta

Con las bebidas de ejemplo. Todo está automatizado en `tests/` (`python -m pytest -q`).

| # | Quién | Mensaje | Resultado esperado |
|---|---|---|---|
| 1 | Cliente nuevo | `2 coca y 1 sprite` | 📍 *¿A qué dirección te lo enviamos? El envío está incluido, sin costo.* No se registra nada todavía. |
| 1b | Cliente nuevo | `Av. Sarmiento 450, Yerba Buena` | *Guardé tu dirección* + ✅ *¡Pedido confirmado!* con envío incluido, la dirección y Total $ 9.400,00. COC1 50→48. |
| 2 | Cliente | `Hola, quiero 2 coca y 3 agua sin gas` | ✅ Confirmado directo (ya tiene dirección). Total $ 9.700,00. |
| 3 | Cliente | `mandame 10 cervezas` | 🟡 En revisión. Al admin: `Stock insuficiente: Cerveza Rubia 1L (pedido 10, disponible 5)` y botones. CER1 sigue en 5. |
| 3b | Admin | `/sumar CER1 10` y ✅ Aprobar | Cliente: *¡Tu pedido fue confirmado!* Total $ 28.000,00. CER1 15→5. |
| 4 | Cliente | `1 fernet` | En revisión por `Precio no vigente: Fernet 750ml`. Admin: `/precio FER1 13000` y `/aprobar ID`. |
| 5 | Cliente | `2 vino` | En revisión (stock 0). Admin: `/rechazar ID Sin stock hasta el lunes` → al cliente le llega ❌ con el motivo. |
| 6 | Cliente | `3 agua` | *¿cuál de estos? Agua Mineral con Gas 2L, Agua Mineral sin Gas 2L*. No se registra. |
| 7 | Cliente | `2 whisky` | *«whisky»: no está en nuestro catálogo*. |
| 8 | Cliente | `2,5 coca` / `-3 sprite` | *la cantidad debe ser un número entero entre 1 y 1000*. |
| 9 | Cliente | `/setstock COC1 0` | ⛔ *Ese comando es solo para el administrador.* |

## 8. Estructura del código

```
app/
  bot.py          capa de Telegram (handlers, botones, arranque); sin lógica de negocio
  service.py      BotService: pedidos, dirección, revisión, comandos (independiente del canal)
  ai_parser.py    intérprete con Gemini (misma firma que parser.parse_order)
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

## 9. Suposiciones

1. El catálogo es **solo de bebidas**.
2. El envío está siempre incluido en el precio: no se cobra aparte.
3. Un pedido en revisión **no reserva stock**; se descuenta recién al aprobar.
4. Al aprobar se usa el precio vigente en ese momento.
5. Cualquiera que escriba al bot puede pedir. Solo los IDs de `ADMIN_CHAT_IDS` gestionan.
6. El bot corre como **un solo proceso**; un candado interno evita descontar dos veces el mismo stock.
7. Los clientes se identifican por canal + ID, así el mismo número en Telegram y WhatsApp (fase 4) no se mezcla.
8. Moneda: solo el símbolo es configurable (`CURRENCY_SYMBOL`); los importes se muestran con formato `1.234,50`.
