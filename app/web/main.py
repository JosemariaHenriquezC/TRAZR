"""
Web de administración de Trazr (FastAPI + plantillas Jinja2, sin JavaScript).

Solo traduce entre formularios HTML y las acciones de admin de BotService
(admin.py): las reglas de negocio no viven acá. Cuando una acción genera avisos
para el cliente (aprobar, rechazar), el Notifier los manda por su canal.

Seguridad: sesión en cookie firmada (SECRET_KEY), httponly y samesite=lax; token
CSRF en cada formulario; contraseñas con hash (auth.py); todo salvo /ingresar, /privacidad
y /salud pide sesión de un admin activo (el webhook de WhatsApp exige la firma de Meta).
"""
from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Form, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from ..admin import LOW_STOCK
from ..config import Settings
from ..models import ESTADO_CANCELADO, ESTADO_CONFIRMADO, ESTADO_RECHAZADO, ESTADO_REVISION, Product
from ..notifier import Notifier
from ..service import BotService
from ..storage.base import StorageError, to_number

log = logging.getLogger(__name__)
HERE = Path(__file__).parent

# Filtros de la lista de pedidos (pestañas)
FILTROS = {
    "revision": lambda o: o.status == ESTADO_REVISION,
    "por_cobrar": lambda o: o.awaiting_payment,
    "pagados": lambda o: o.status == ESTADO_CONFIRMADO and o.payment_status != "pendiente",
    "rechazados": lambda o: o.status in (ESTADO_RECHAZADO, ESTADO_CANCELADO),
}
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre",
         "octubre", "noviembre", "diciembre"]
DIAS = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]


class LoginRequired(Exception):
    pass


def create_app(svc: BotService, notifier: Notifier, settings: Settings, lifespan=None) -> FastAPI:
    app = FastAPI(title="Trazr Admin", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    secret = settings.secret_key or secrets.token_hex(32)
    if not settings.secret_key:
        log.warning("SECRET_KEY vacía: uso una clave temporal (al reiniciar hay que volver a ingresar).")
    app.add_middleware(SessionMiddleware, secret_key=secret, session_cookie="trazr_admin",
                       max_age=12 * 3600, same_site="lax", https_only=False)
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    templates = Jinja2Templates(directory=HERE / "templates")
    app.state.svc, app.state.notifier = svc, notifier

    # ------------------------------------------------------------ filtros
    def fecha(stamp: str) -> str:
        try:
            d = datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S")
        except (ValueError, TypeError):
            return stamp or ""
        today = svc.today()
        if d.date() == today:
            return f"hoy {d:%H:%M}"
        if d.date() == today - timedelta(days=1):
            return f"ayer {d:%H:%M}"
        return f"{d:%d/%m %H:%M}"

    templates.env.filters["money"] = svc.money
    templates.env.filters["qty"] = svc.qty
    templates.env.filters["fecha"] = fecha
    templates.env.filters["urlq"] = lambda v: quote(str(v), safe="")
    templates.env.globals["LOW_STOCK"] = LOW_STOCK

    # ------------------------------------------------------------ sesión
    def csrf_token(request: Request) -> str:
        tok = request.session.get("csrf")
        if not tok:
            tok = request.session["csrf"] = secrets.token_urlsafe(24)
        return tok

    def check_csrf(request: Request, token: str) -> None:
        if not token or not secrets.compare_digest(token, request.session.get("csrf", "")):
            raise LoginRequired()

    def current_admin(request: Request):
        username = request.session.get("admin")
        admin = svc.repo.get_admin(username) if username else None
        if admin is None or not admin.active:
            request.session.pop("admin", None)
            raise LoginRequired()
        return admin

    def flash(request: Request, message: str, ok: bool = True) -> None:
        request.session["flash"] = {"message": message, "ok": ok}

    def render(request: Request, name: str, admin=None, **ctx) -> HTMLResponse:
        unread = 0
        pending = 0
        if admin is not None:
            unread = len(svc.repo.list_notices(limit=500, unread_only=True))
            pending = sum(1 for o in svc.repo.list_orders() if o.status == ESTADO_REVISION)
        return templates.TemplateResponse(request, name, {
            "admin": admin, "csrf": csrf_token(request), "flash": request.session.pop("flash", None),
            "version": svc.version() if admin is not None else "",
            "unread": unread, "pending_count": pending, "shop": settings.business.name or "Trazr", **ctx})

    def back(url: str) -> RedirectResponse:
        return RedirectResponse(url, status_code=303)

    @app.exception_handler(LoginRequired)
    async def _login_required(request: Request, exc: LoginRequired):
        return back("/ingresar")

    @app.exception_handler(StorageError)
    async def _storage(request: Request, exc: StorageError):
        log.error("Falla de la base de datos en la web: %s", exc)
        return HTMLResponse(f"<p>No pude acceder a la base de datos: {exc}. Probá de nuevo en unos minutos.</p>",
                            status_code=503)

    # ------------------------------------------------------------ ingreso
    @app.get("/ingresar", response_class=HTMLResponse)
    def login_form(request: Request):
        return render(request, "login.html", error=False, username="")

    @app.post("/ingresar")
    def login(request: Request, usuario: str = Form(""), clave: str = Form(""), csrf_token: str = Form("")):
        check_csrf(request, csrf_token)
        admin = svc.authenticate(usuario, clave)
        if admin is None:
            log.info("Ingreso fallido para «%s»", usuario[:40])
            return render(request, "login.html", error=True, username=usuario)
        request.session.clear()
        request.session["admin"] = admin.username
        return back("/")

    @app.post("/salir")
    def logout(request: Request, csrf_token: str = Form("")):
        check_csrf(request, csrf_token)
        request.session.clear()
        return back("/ingresar")

    # ------------------------------------------------------------ tablero
    @app.get("/", response_class=HTMLResponse)
    def dashboard(request: Request):
        admin = current_admin(request)
        d = svc.dashboard()
        now = svc.now()
        hour = now.hour
        greeting = "Buen día" if hour < 13 else "Buenas tardes" if hour < 20 else "Buenas noches"
        today_label = f"{DIAS[now.weekday()]} {now.day} de {MESES[now.month - 1]}"
        notices = svc.repo.list_notices(limit=8)
        return render(request, "tablero.html", admin, d=d, greeting=greeting, today_label=today_label,
                      notices=notices, page="tablero")

    @app.post("/avisos/leidos")
    def notices_read(request: Request, csrf_token: str = Form("")):
        current_admin(request)
        check_csrf(request, csrf_token)
        svc.repo.mark_notices_read()
        flash(request, "Avisos marcados como leídos.")
        return back("/")

    # ------------------------------------------------------------ pedidos
    @app.get("/pedidos", response_class=HTMLResponse)
    def orders(request: Request, estado: str = "revision", q: str = ""):
        admin = current_admin(request)
        all_orders = list(reversed(svc.repo.list_orders()))
        counts = {k: sum(1 for o in all_orders if f(o)) for k, f in FILTROS.items()}
        keep = FILTROS.get(estado)
        rows = [o for o in all_orders if keep(o)] if keep else all_orders
        q_norm = q.strip().lower()
        if q_norm:
            rows = [o for o in rows if q_norm in o.order_id.lower() or q_norm in o.customer_name.lower()
                    or any(q_norm in l.name.lower() for l in o.lines)]
        return render(request, "pedidos.html", admin, orders=rows, estado=estado, q=q, counts=counts,
                      total=len(all_orders), page="pedidos")

    @app.get("/pedidos/{order_id}", response_class=HTMLResponse)
    def order_detail(request: Request, order_id: str):
        admin = current_admin(request)
        order = svc.repo.get_order(order_id)
        if order is None:
            flash(request, f"No encontré el pedido {order_id}.", ok=False)
            return back("/pedidos")
        products = {p.sku.upper(): p for p in svc.repo.list_products()}
        lines = []
        for l in order.lines:
            p = products.get(l.sku.upper())
            lines.append({"line": l, "product": p, "stock_ok": p is not None and p.stock >= l.qty})
        customer = next((c for c in svc.repo.list_customers()
                         if c.channel == order.channel and c.external_id == str(order.customer_id)), None)
        customer_orders = sum(1 for o in svc.repo.list_orders()
                              if o.customer_id == order.customer_id and o.channel == order.channel)
        return render(request, "pedido.html", admin, order=order, lines=lines,
                      payment_hours=settings.rules.payment_hours,
                      customer=customer, customer_orders=customer_orders, page="pedidos")

    @app.post("/pedidos/{order_id}/aprobar")
    async def approve(request: Request, order_id: str):
        admin = current_admin(request)
        form = await request.form()
        check_csrf(request, str(form.get("csrf_token", "")))
        result = await run_in_threadpool(svc.approve, order_id, admin.name)
        if result.ok:
            sent = await notifier.send(result.notify)
            msg = result.message if sent else result.message.replace("se avisó al cliente",
                                                                       "NO se pudo avisar al cliente por el chat")
            flash(request, msg, ok=True)
        else:
            flash(request, result.message, ok=False)
        return back(f"/pedidos/{order_id}")

    @app.post("/pedidos/{order_id}/rechazar")
    async def reject(request: Request, order_id: str, motivo: str = Form(""), csrf_token: str = Form("")):
        admin = current_admin(request)
        check_csrf(request, csrf_token)
        result = await run_in_threadpool(svc.reject, order_id, admin.name, motivo)
        if result.ok:
            sent = await notifier.send(result.notify)
            msg = result.message if sent else result.message.replace("le avisamos al cliente el motivo",
                                                                       "NO se pudo avisar al cliente por el chat")
            flash(request, msg, ok=True)
        else:
            flash(request, result.message, ok=False)
        return back(f"/pedidos/{order_id}")

    async def _admin_action(request: Request, order_id: str, fn, *args, volver: str = ""):
        """Corre una acción de admin, avisa al cliente por su canal y deja el resultado en pantalla."""
        result = await run_in_threadpool(fn, *args)
        if result.ok:
            sent = await notifier.send(result.notify)
            flash(request, result.message if sent or not result.notify
                  else result.message + " (No se pudo avisar al cliente por el chat.)", ok=True)
        else:
            flash(request, result.message, ok=False)
        return back(volver if volver in ("/", "/pedidos?estado=por_cobrar") else f"/pedidos/{order_id}")

    @app.post("/pedidos/{order_id}/pago")
    async def confirm_payment(request: Request, order_id: str, csrf_token: str = Form(""), volver: str = Form("")):
        admin = current_admin(request)
        check_csrf(request, csrf_token)
        return await _admin_action(request, order_id, svc.confirm_payment, order_id, admin.name, volver=volver)

    @app.post("/pedidos/{order_id}/cancelar")
    async def cancel_unpaid(request: Request, order_id: str, motivo: str = Form(""), csrf_token: str = Form("")):
        admin = current_admin(request)
        check_csrf(request, csrf_token)
        return await _admin_action(request, order_id, svc.cancel_unpaid, order_id, admin.name, motivo)

    # ------------------------------------------------------------ productos
    @app.get("/productos", response_class=HTMLResponse)
    def products(request: Request, filtro: str = "todos", q: str = "", editar: str = "", nuevo: int = 0):
        admin = current_admin(request)
        items = svc.repo.list_products()
        counts = {"poco": sum(1 for p in items if p.active and p.stock < LOW_STOCK),
                  "inactivos": sum(1 for p in items if not p.active)}
        if filtro == "poco":
            items = [p for p in items if p.active and p.stock < LOW_STOCK]
        elif filtro == "inactivos":
            items = [p for p in items if not p.active]
        q_norm = q.strip().lower()
        if q_norm:
            items = [p for p in items if q_norm in p.name.lower() or q_norm in p.sku.lower()
                     or any(q_norm in a.lower() for a in p.aliases)]
        editing = svc.repo.get_product(editar) if editar else None
        categories = sorted({p.category for p in svc.repo.list_products() if p.category})
        return render(request, "productos.html", admin, products=items, filtro=filtro, q=q, counts=counts,
                      total=len(svc.repo.list_products()), editing=editing, new=bool(nuevo),
                      categories=categories, page="productos")

    @app.post("/productos/{sku}/sumar")
    def add_stock(request: Request, sku: str, cantidad: str = Form(""), csrf_token: str = Form(""),
                  volver: str = Form("/productos")):
        current_admin(request)
        check_csrf(request, csrf_token)
        n = to_number(cantidad)
        result = (svc.add_stock(sku, int(n)) if n is not None and float(n).is_integer()
                  else None)
        if result is None:
            flash(request, "Escribí una cantidad entera para sumar (o negativa para restar).", ok=False)
        else:
            flash(request, result.message, ok=result.ok)
        return back(volver if volver.startswith("/productos") else "/productos")

    @app.post("/productos/guardar")
    def save_product(request: Request, csrf_token: str = Form(""), es_nuevo: str = Form("0"),
                     sku: str = Form(""), nombre: str = Form(""), alias: str = Form(""),
                     categoria: str = Form(""), precio: str = Form(""),
                     stock: str = Form(""), activo: str = Form("1"),
                     unidades_caja: str = Form(""), nombre_caja: str = Form("caja")):
        current_admin(request)
        check_csrf(request, csrf_token)
        is_new = es_nuevo == "1"
        price, qty = to_number(precio), to_number(stock)
        pack = to_number(unidades_caja) if unidades_caja.strip() else 0
        error = None
        if pack is None or pack < 0 or int(pack) != pack:
            error = "Las unidades por caja tienen que ser un número entero (o dejalo vacío)."
        if price is None:
            error = "Escribí un precio válido (por ejemplo 3200 o 3.200,50)."
        elif qty is None and not error:
            error = "Escribí el stock en unidades."
        if error:
            flash(request, error, ok=False)
            return back("/productos?nuevo=1" if is_new else f"/productos?editar={quote(sku)}")
        product = Product(sku=sku, name=nombre, aliases=alias.split(","), stock=qty, price=price,
                          active=activo == "1", category=categoria.strip(),
                          pack_size=int(pack), pack_name=(nombre_caja or "caja").strip().lower() or "caja")
        result = svc.save_product(product, is_new)
        flash(request, result.message, ok=result.ok)
        if not result.ok:
            return back("/productos?nuevo=1" if is_new else f"/productos?editar={quote(sku)}")
        return back("/productos")

    # ------------------------------------------------------------ clientes
    @app.get("/clientes", response_class=HTMLResponse)
    def customers(request: Request, q: str = "", c: str = ""):
        admin = current_admin(request)
        orders = svc.repo.list_orders()
        rows = []
        for cu in svc.repo.list_customers():
            mine = [o for o in orders if o.channel == cu.channel and str(o.customer_id) == cu.external_id]
            confirmed = [o for o in mine if o.status == ESTADO_CONFIRMADO]
            rows.append({"customer": cu, "key": f"{cu.channel}:{cu.external_id}", "orders": mine,
                         "count": len(mine), "spent": round(sum(o.total for o in confirmed), 2),
                         "verified": any(o.is_paid for o in mine),
                         "last": mine[-1].created_at if mine else ""})
        rows.sort(key=lambda r: r["last"], reverse=True)
        q_norm = q.strip().lower()
        if q_norm:
            rows = [r for r in rows if q_norm in r["customer"].name.lower() or q_norm in r["customer"].address.lower()]
        selected = next((r for r in rows if r["key"] == c), rows[0] if rows else None)
        if selected is not None:
            counter: dict[str, int] = {}
            for o in selected["orders"]:
                for l in o.lines:
                    counter[l.name] = counter.get(l.name, 0) + l.qty
            selected["top"] = [n for n, _ in sorted(counter.items(), key=lambda kv: -kv[1])[:3]]
        return render(request, "clientes.html", admin, rows=rows, q=q, selected=selected, page="clientes")

    @app.get("/cambios")
    def changes(request: Request) -> Response:
        """Firma del estado actual: la página la consulta cada pocos segundos y se refresca sola
        cuando cambia (entra un pedido, cambia el stock, llega un aviso)."""
        current_admin(request)
        return Response(svc.version(), media_type="text/plain", headers={"Cache-Control": "no-store"})

    @app.get("/privacidad", response_class=HTMLResponse)
    def privacy(request: Request):
        """Política de privacidad: pública (Meta la pide para publicar la app de WhatsApp).
        Las instrucciones para borrar datos están en /privacidad#borrar."""
        b = settings.business
        return templates.TemplateResponse(request, "privacidad.html", {
            "shop": b.name or "Trazr", "contact": b.contact, "zone": b.delivery_zone,
            "updated": "12 de noviembre de 2026"})

    @app.get("/salud")
    def health() -> Response:
        return Response("ok", media_type="text/plain")

    return app
