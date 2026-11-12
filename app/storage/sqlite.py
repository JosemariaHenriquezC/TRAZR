"""
Base de datos SQLite: productos (bebidas), clientes, pedidos y sus renglones.

Es un único archivo (por defecto data/trazr.db). Se crea solo la primera vez
que arranca el bot. Para mirarlo o editarlo a mano sirve cualquier visor de
SQLite (por ejemplo «DB Browser for SQLite»).

Reglas que cuida la propia base, además de service.py y rules.py:
  - el stock nunca queda negativo;
  - un pedido no se puede guardar sin dirección de envío;
  - el estado de un pedido solo puede ser CONFIRMADO, EN REVISION o RECHAZADO.
"""
from __future__ import annotations

import logging
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from ..models import ESTADOS, Admin, Customer, Notice, Order, OrderLine, Product
from .base import Repository, StorageError

log = logging.getLogger(__name__)

SCHEMA_VERSION = 2

_ESTADOS_SQL = ", ".join(f"'{e}'" for e in ESTADOS)

def _pedidos_sql(name: str) -> str:
    return f"""CREATE TABLE IF NOT EXISTS {name} (
    pedido_id         TEXT PRIMARY KEY,
    fecha             TEXT NOT NULL,
    canal             TEXT NOT NULL,
    cliente_id        TEXT NOT NULL,
    cliente_nombre    TEXT NOT NULL DEFAULT '',
    mensaje_original  TEXT NOT NULL DEFAULT '',
    direccion_envio   TEXT NOT NULL CHECK (trim(direccion_envio) <> ''),
    costo_envio       REAL NOT NULL DEFAULT 0,
    total             REAL NOT NULL,
    estado            TEXT NOT NULL CHECK (estado IN ({_ESTADOS_SQL})),
    motivo            TEXT NOT NULL DEFAULT '',
    actualizado       TEXT NOT NULL,
    revisado_por      TEXT NOT NULL DEFAULT '',
    pago_medio        TEXT NOT NULL DEFAULT '',        -- efectivo | transferencia
    pago_estado       TEXT NOT NULL DEFAULT '',        -- pendiente | pagado
    pago_fecha        TEXT NOT NULL DEFAULT '',
    pago_por          TEXT NOT NULL DEFAULT ''         -- admin que confirmó el pago
);"""


PEDIDOS_COLUMNS = ["pedido_id", "fecha", "canal", "cliente_id", "cliente_nombre", "mensaje_original",
                   "direccion_envio", "costo_envio", "total", "estado", "motivo", "actualizado",
                   "revisado_por", "pago_medio", "pago_estado", "pago_fecha", "pago_por"]

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS productos (
    sku            TEXT PRIMARY KEY COLLATE NOCASE,
    producto       TEXT NOT NULL CHECK (trim(producto) <> ''),
    categoria      TEXT NOT NULL DEFAULT '',
    alias          TEXT NOT NULL DEFAULT '',          -- separados por coma
    stock          INTEGER NOT NULL DEFAULT 0 CHECK (stock >= 0),
    precio         REAL NOT NULL DEFAULT 0 CHECK (precio >= 0),
    activo         INTEGER NOT NULL DEFAULT 1 CHECK (activo IN (0, 1)),
    actualizado    TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS clientes (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    canal        TEXT NOT NULL,                       -- telegram | whatsapp
    externo_id   TEXT NOT NULL,                       -- ID del chat / teléfono en ese canal
    nombre       TEXT NOT NULL DEFAULT '',
    direccion    TEXT NOT NULL DEFAULT '',
    creado       TEXT NOT NULL,
    actualizado  TEXT NOT NULL,
    UNIQUE (canal, externo_id)
);

{_pedidos_sql("pedidos")}
CREATE INDEX IF NOT EXISTS ix_pedidos_cliente ON pedidos (canal, cliente_id);
CREATE INDEX IF NOT EXISTS ix_pedidos_estado ON pedidos (estado);

CREATE TABLE IF NOT EXISTS pedido_items (
    pedido_id        TEXT NOT NULL REFERENCES pedidos (pedido_id) ON DELETE CASCADE,
    renglon          INTEGER NOT NULL,
    sku              TEXT NOT NULL,
    producto         TEXT NOT NULL,
    cantidad         INTEGER NOT NULL CHECK (cantidad > 0),
    precio_unitario  REAL NOT NULL,
    subtotal         REAL NOT NULL,
    PRIMARY KEY (pedido_id, renglon)
);

-- Los mensajes del chat NO se guardan en la base (la memoria de Gemini vive en RAM).
-- Bases creadas antes guardaban mensajes: se borran.
DROP TABLE IF EXISTS mensajes;

-- Avisos para el admin: se ven en la web (el admin no recibe nada por chat).
CREATE TABLE IF NOT EXISTS avisos (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    fecha      TEXT NOT NULL,
    tipo       TEXT NOT NULL,
    titulo     TEXT NOT NULL,
    detalle    TEXT NOT NULL DEFAULT '',
    pedido_id  TEXT NOT NULL DEFAULT '',
    leido      INTEGER NOT NULL DEFAULT 0 CHECK (leido IN (0, 1))
);
CREATE INDEX IF NOT EXISTS ix_avisos_leido ON avisos (leido, id);

-- Usuarios de la web de administración (contraseña con hash scrypt, nunca en texto plano).
CREATE TABLE IF NOT EXISTS admins (
    usuario      TEXT PRIMARY KEY COLLATE NOCASE,
    nombre       TEXT NOT NULL,
    clave_hash   TEXT NOT NULL,
    activo       INTEGER NOT NULL DEFAULT 1 CHECK (activo IN (0, 1)),
    creado       TEXT NOT NULL,
    actualizado  TEXT NOT NULL
);

-- Pedido ya interpretado que espera la dirección del cliente (uno por cliente).
CREATE TABLE IF NOT EXISTS pedidos_pendientes (
    canal       TEXT NOT NULL,
    externo_id  TEXT NOT NULL,
    mensaje     TEXT NOT NULL,
    creado      TEXT NOT NULL,
    PRIMARY KEY (canal, externo_id)
);
"""


def _friendly(exc: sqlite3.Error) -> str:
    text = str(exc)
    if isinstance(exc, sqlite3.IntegrityError):
        if "stock" in text:
            return "La operación dejaría el stock negativo."
        if "direccion_envio" in text:
            return "El pedido no tiene dirección de envío."
        return f"Dato inválido para la base de datos: {text}"
    if isinstance(exc, sqlite3.OperationalError) and "locked" in text:
        return "La base de datos está ocupada; reintentá en unos segundos."
    return f"Error de la base de datos: {exc.__class__.__name__}: {text}"


class SQLiteRepository(Repository):
    def __init__(self, path: str):
        self.path = path
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        try:
            # Una sola conexión compartida, protegida por un candado: el bot corre
            # como un proceso y las llamadas llegan desde varios hilos.
            self._conn = sqlite3.connect(path, timeout=10, check_same_thread=False,
                                         isolation_level=None)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA foreign_keys = ON")
            if path != ":memory:":
                self._conn.execute("PRAGMA journal_mode = WAL")
            self._conn.executescript(SCHEMA)
            self._migrate()
            self._conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        except sqlite3.Error as exc:
            raise StorageError(f"No pude abrir la base de datos {path!r}: {exc}") from exc
        self._lock = threading.RLock()

    def _migrate(self) -> None:
        """Bases creadas antes del pago: se rehace `pedidos` con las columnas de pago y el
        estado CANCELADO (SQLite no deja cambiar un CHECK). No se pierde ningún dato."""
        sql = self._conn.execute("SELECT sql FROM sqlite_master WHERE name = 'pedidos'").fetchone()[0]
        if "pago_medio" in sql and "CANCELADO" in sql:
            return
        have = [r[1] for r in self._conn.execute("PRAGMA table_info(pedidos)")]
        cols = [c for c in PEDIDOS_COLUMNS if c in have]
        self._conn.execute("PRAGMA foreign_keys = OFF")
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            self._conn.execute(_pedidos_sql("pedidos_nueva"))
            self._conn.execute(f"INSERT INTO pedidos_nueva ({', '.join(cols)}) "
                               f"SELECT {', '.join(cols)} FROM pedidos ORDER BY rowid")
            # Los pedidos confirmados de antes se dan por pagados (no hay forma de saberlo).
            self._conn.execute("UPDATE pedidos_nueva SET pago_estado = 'pagado' WHERE estado = 'CONFIRMADO' "
                               "AND pago_estado = ''")
            # Los que esperaban revisión van a pedir pago cuando se aprueben (efectivo por defecto).
            self._conn.execute("UPDATE pedidos_nueva SET pago_estado = 'pendiente', pago_medio = 'efectivo' "
                               "WHERE estado = 'EN REVISION' AND pago_estado = ''")
            self._conn.execute("DROP TABLE pedidos")
            self._conn.execute("ALTER TABLE pedidos_nueva RENAME TO pedidos")
            self._conn.execute("CREATE INDEX IF NOT EXISTS ix_pedidos_cliente ON pedidos (canal, cliente_id)")
            self._conn.execute("CREATE INDEX IF NOT EXISTS ix_pedidos_estado ON pedidos (estado)")
            self._conn.execute("COMMIT")
        except sqlite3.Error:
            self._conn.execute("ROLLBACK")
            raise
        finally:
            self._conn.execute("PRAGMA foreign_keys = ON")
        log.info("Base migrada: los pedidos ahora guardan el medio y el estado del pago.")

    def close(self) -> None:
        self._conn.close()

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        """Transacción: o se guarda todo o no se guarda nada."""
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                try:
                    yield self._conn
                except BaseException:
                    self._conn.execute("ROLLBACK")
                    raise
                self._conn.execute("COMMIT")
            except sqlite3.Error as exc:
                log.exception("Fallo de SQLite")
                raise StorageError(_friendly(exc)) from exc

    def _query(self, sql: str, params=()) -> list[sqlite3.Row]:
        with self._lock:
            try:
                return self._conn.execute(sql, params).fetchall()
            except sqlite3.Error as exc:
                log.exception("Fallo de SQLite")
                raise StorageError(_friendly(exc)) from exc

    # ------------------------------------------------------------ productos
    @staticmethod
    def _product(r: sqlite3.Row) -> Product:
        return Product(
            sku=r["sku"], name=r["producto"],
            aliases=[a.strip() for a in (r["alias"] or "").split(",") if a.strip()],
            stock=float(r["stock"]), price=float(r["precio"]),
            active=bool(r["activo"]), category=r["categoria"],
        )

    def list_products(self) -> list[Product]:
        rows = self._query("SELECT * FROM productos ORDER BY categoria, producto")
        return [self._product(r) for r in rows]

    def get_product(self, sku: str) -> Product | None:
        rows = self._query("SELECT * FROM productos WHERE sku = ?", (sku.strip(),))
        return self._product(rows[0]) if rows else None

    def upsert_product(self, p: Product, stamp: str) -> None:
        with self._tx() as c:
            c.execute(
                """INSERT INTO productos (sku, producto, categoria, alias, stock, precio,
                                         activo, actualizado)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT (sku) DO UPDATE SET
                     producto = excluded.producto, categoria = excluded.categoria,
                     alias = excluded.alias, stock = excluded.stock, precio = excluded.precio,
                     activo = excluded.activo,
                     actualizado = excluded.actualizado""",
                (p.sku.strip().upper(), p.name.strip(), p.category.strip(), ", ".join(p.aliases),
                 int(p.stock), float(p.price), int(p.active), stamp))

    def _require_product(self, sku: str) -> Product:
        p = self.get_product(sku)
        if p is None:
            raise KeyError(sku)
        return p

    def set_stock(self, sku: str, new_stock: float, stamp: str) -> Product:
        p = self._require_product(sku)
        with self._tx() as c:
            c.execute("UPDATE productos SET stock = ?, actualizado = ? WHERE sku = ?",
                      (int(new_stock), stamp, p.sku))
        return self._require_product(p.sku)

    def apply_stock_deltas(self, deltas: dict[str, float], stamp: str) -> None:
        with self._tx() as c:
            for sku, delta in deltas.items():
                cur = c.execute("UPDATE productos SET stock = stock + ?, actualizado = ? WHERE sku = ?",
                                (int(delta), stamp, sku))
                if cur.rowcount == 0:
                    raise StorageError(f"No existe el SKU {sku!r} en la base de datos.")

    def set_price(self, sku: str, price: float, stamp: str) -> Product:
        p = self._require_product(sku)
        with self._tx() as c:
            c.execute("UPDATE productos SET precio = ?, actualizado = ? WHERE sku = ?",
                      (float(price), stamp, p.sku))
        return self._require_product(p.sku)

    # ------------------------------------------------------------- clientes
    def get_customer(self, channel: str, external_id: str) -> Customer | None:
        rows = self._query("SELECT * FROM clientes WHERE canal = ? AND externo_id = ?",
                           (channel, str(external_id)))
        if not rows:
            return None
        r = rows[0]
        return Customer(channel=r["canal"], external_id=r["externo_id"], name=r["nombre"],
                        address=r["direccion"])

    def list_customers(self) -> list[Customer]:
        rows = self._query("SELECT * FROM clientes ORDER BY nombre COLLATE NOCASE")
        return [Customer(channel=r["canal"], external_id=r["externo_id"], name=r["nombre"],
                         address=r["direccion"]) for r in rows]

    def save_customer(self, cu: Customer, stamp: str) -> Customer:
        with self._tx() as c:
            c.execute(
                """INSERT INTO clientes (canal, externo_id, nombre, direccion, creado, actualizado)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT (canal, externo_id) DO UPDATE SET
                     nombre = excluded.nombre, direccion = excluded.direccion,
                     actualizado = excluded.actualizado""",
                (cu.channel, str(cu.external_id), cu.name, cu.address.strip(), stamp, stamp))
        return cu

    # -------------------------------------------------- pedidos pendientes
    def set_pending_order(self, channel: str, external_id: str, text: str, stamp: str) -> None:
        with self._tx() as c:
            c.execute("""INSERT INTO pedidos_pendientes (canal, externo_id, mensaje, creado)
                         VALUES (?, ?, ?, ?)
                         ON CONFLICT (canal, externo_id) DO UPDATE SET
                           mensaje = excluded.mensaje, creado = excluded.creado""",
                      (channel, str(external_id), text, stamp))

    def get_pending_order(self, channel: str, external_id: str) -> tuple[str, str] | None:
        rows = self._query("SELECT mensaje, creado FROM pedidos_pendientes "
                           "WHERE canal = ? AND externo_id = ?", (channel, str(external_id)))
        return (rows[0]["mensaje"], rows[0]["creado"]) if rows else None

    def clear_pending_order(self, channel: str, external_id: str) -> None:
        with self._tx() as c:
            c.execute("DELETE FROM pedidos_pendientes WHERE canal = ? AND externo_id = ?",
                      (channel, str(external_id)))

    # --------------------------------------------------------------- avisos
    def add_notice(self, kind: str, title: str, detail: str, order_id: str, stamp: str) -> None:
        with self._tx() as c:
            c.execute("INSERT INTO avisos (fecha, tipo, titulo, detalle, pedido_id) VALUES (?, ?, ?, ?, ?)",
                      (stamp, kind, title, detail, order_id or ""))

    def list_notices(self, limit: int = 50, unread_only: bool = False) -> list[Notice]:
        where = "WHERE leido = 0" if unread_only else ""
        rows = self._query(f"SELECT * FROM avisos {where} ORDER BY id DESC LIMIT ?", (limit,))
        return [Notice(id=r["id"], created_at=r["fecha"], kind=r["tipo"], title=r["titulo"],
                       detail=r["detalle"], order_id=r["pedido_id"], read=bool(r["leido"])) for r in rows]

    def mark_notices_read(self, ids: list[int] | None = None) -> None:
        with self._tx() as c:
            if ids is None:
                c.execute("UPDATE avisos SET leido = 1 WHERE leido = 0")
            else:
                c.executemany("UPDATE avisos SET leido = 1 WHERE id = ?", [(int(i),) for i in ids])

    # --------------------------------------------------------------- admins
    @staticmethod
    def _admin(r: sqlite3.Row) -> Admin:
        return Admin(username=r["usuario"], name=r["nombre"], password_hash=r["clave_hash"],
                     active=bool(r["activo"]))

    def get_admin(self, username: str) -> Admin | None:
        rows = self._query("SELECT * FROM admins WHERE usuario = ?", (username.strip(),))
        return self._admin(rows[0]) if rows else None

    def list_admins(self) -> list[Admin]:
        return [self._admin(r) for r in self._query("SELECT * FROM admins ORDER BY usuario")]

    def save_admin(self, a: Admin, stamp: str) -> None:
        with self._tx() as c:
            c.execute("""INSERT INTO admins (usuario, nombre, clave_hash, activo, creado, actualizado)
                         VALUES (?, ?, ?, ?, ?, ?)
                         ON CONFLICT (usuario) DO UPDATE SET nombre = excluded.nombre,
                           clave_hash = excluded.clave_hash, activo = excluded.activo,
                           actualizado = excluded.actualizado""",
                      (a.username.strip().lower(), a.name.strip(), a.password_hash, int(a.active), stamp, stamp))

    # -------------------------------------------------------------- pedidos
    @staticmethod
    def _insert_lines(c: sqlite3.Connection, order_id: str, lines: list[OrderLine]) -> None:
        c.executemany(
            "INSERT INTO pedido_items (pedido_id, renglon, sku, producto, cantidad, precio_unitario, subtotal) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(order_id, n, l.sku, l.name, l.qty, l.unit_price, l.subtotal)
             for n, l in enumerate(lines, start=1)])

    def add_order(self, o: Order) -> None:
        with self._tx() as c:
            c.execute(
                """INSERT INTO pedidos (pedido_id, fecha, canal, cliente_id, cliente_nombre,
                                        mensaje_original, direccion_envio, costo_envio, total,
                                        estado, motivo, actualizado, revisado_por,
                                        pago_medio, pago_estado, pago_fecha, pago_por)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (o.order_id, o.created_at, o.channel, str(o.customer_id), o.customer_name,
                 o.original_text, o.address, o.shipping_cost, o.total, o.status, o.reason,
                 o.updated_at or o.created_at, o.reviewed_by,
                 o.payment_method, o.payment_status, o.paid_at, o.paid_by))
            self._insert_lines(c, o.order_id, o.lines)

    def _orders(self, where: str = "", params=()) -> list[Order]:
        rows = self._query(f"SELECT rowid AS _n, * FROM pedidos {where} ORDER BY _n", params)
        if not rows:
            return []
        ids = [r["pedido_id"] for r in rows]
        lines: dict[str, list[OrderLine]] = {i: [] for i in ids}
        marks = ", ".join("?" * len(ids))
        for it in self._query(f"SELECT * FROM pedido_items WHERE pedido_id IN ({marks}) "
                              "ORDER BY pedido_id, renglon", ids):
            lines[it["pedido_id"]].append(OrderLine(it["sku"], it["producto"], int(it["cantidad"]),
                                                    float(it["precio_unitario"])))
        out = []
        for r in rows:
            try:
                customer_id = int(r["cliente_id"])
            except ValueError:
                customer_id = 0
            out.append(Order(
                order_id=r["pedido_id"], created_at=r["fecha"], customer_id=customer_id,
                customer_name=r["cliente_nombre"], original_text=r["mensaje_original"],
                lines=lines[r["pedido_id"]], status=r["estado"], reason=r["motivo"],
                updated_at=r["actualizado"], reviewed_by=r["revisado_por"],
                address=r["direccion_envio"], shipping_cost=float(r["costo_envio"]),
                channel=r["canal"], payment_method=r["pago_medio"], payment_status=r["pago_estado"],
                paid_at=r["pago_fecha"], paid_by=r["pago_por"]))
        return out

    def list_orders(self) -> list[Order]:
        return self._orders()

    def get_order(self, order_id: str) -> Order | None:
        found = self._orders("WHERE upper(pedido_id) = upper(?)", (order_id.strip(),))
        return found[0] if found else None

    def update_order(self, o: Order) -> Order:
        if self.get_order(o.order_id) is None:
            raise KeyError(o.order_id)
        with self._tx() as c:
            c.execute("""UPDATE pedidos SET direccion_envio = ?, costo_envio = ?, total = ?, estado = ?,
                                          motivo = ?, actualizado = ?, revisado_por = ?,
                                          pago_medio = ?, pago_estado = ?, pago_fecha = ?, pago_por = ?
                         WHERE pedido_id = ?""",
                      (o.address, o.shipping_cost, o.total, o.status, o.reason, o.updated_at,
                       o.reviewed_by, o.payment_method, o.payment_status, o.paid_at, o.paid_by,
                       o.order_id))
            c.execute("DELETE FROM pedido_items WHERE pedido_id = ?", (o.order_id,))
            self._insert_lines(c, o.order_id, o.lines)
        return self.get_order(o.order_id)

    def update_order_status(self, order_id: str, status: str, reason: str,
                            reviewed_by: str, stamp: str,
                            lines: list[OrderLine] | None = None) -> Order:
        o = self.get_order(order_id)
        if o is None:
            raise KeyError(order_id)
        with self._tx() as c:
            c.execute("UPDATE pedidos SET estado = ?, motivo = ?, actualizado = ?, revisado_por = ? "
                      "WHERE pedido_id = ?", (status, reason, stamp, reviewed_by, o.order_id))
            if lines is not None:
                o.lines = lines
                c.execute("DELETE FROM pedido_items WHERE pedido_id = ?", (o.order_id,))
                self._insert_lines(c, o.order_id, lines)
                c.execute("UPDATE pedidos SET total = ? WHERE pedido_id = ?", (o.total, o.order_id))
        return self.get_order(o.order_id)
