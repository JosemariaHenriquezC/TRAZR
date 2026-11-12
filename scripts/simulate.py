"""
Simulador de consola: probá el bot SIN Telegram (misma lógica, misma base de datos).

    python -m scripts.simulate

Escribí mensajes como si fueras un cliente. Los botones se tocan con :dato
(el bot los muestra, por ejemplo  [✅ Confirmar = :cf 6312e8]).

Acciones del admin (las mismas que hace la web):
    :avisos                     → avisos para el admin
    :revision                   → pedidos en revisión
    :aprobar ID [SKU=precio]    → aprobar (opcional: precio nuevo, ej. FER1=13000)
    :rechazar ID motivo         → rechazar con motivo
    :pago ID                    → confirmar que se recibió el pago
    :sumar SKU cantidad         → sumar (o restar) stock
    :salir
"""
from __future__ import annotations

import re
import sys

from app.config import ConfigError, load_settings
from app.service import BotService, User
from app.storage import build_repository
from app.storage.base import to_number

CLIENT = User(id=2, name="Cliente Demo")


def strip_html(text: str) -> str:
    return re.sub(r"</?(b|i|code)>", "", text).replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")


def show(out):
    for m in out:
        print("\n  ── bot → cliente ──")
        print("  " + strip_html(m.text).replace("\n", "\n  "))
        if m.buttons:
            print("  [" + "] [".join(f"{t} = :{d.split(':')[0]} {d.split(':')[1]}" for t, d in m.buttons) + "]")


def admin(svc: BotService, text: str) -> None:
    cmd, *args = text[1:].split()
    if cmd == "avisos":
        for n in svc.repo.list_notices(limit=10):
            print(f"  {'•' if not n.read else ' '} {n.created_at} {n.title} — {n.detail}")
        svc.repo.mark_notices_read()
        return
    if cmd == "revision":
        for o in svc.repo.list_orders():
            if o.status == "EN REVISION":
                print(f"  {o.order_id}  {o.customer_name}  {svc.money(o.total)}  ({o.reason})")
        return
    if cmd == "aprobar" and args:
        prices = {k.upper(): to_number(v) for k, v in (a.split("=", 1) for a in args[1:] if "=" in a)}
        r = svc.approve(args[0], "Admin (simulador)", prices)
    elif cmd == "rechazar" and len(args) >= 2:
        r = svc.reject(args[0], "Admin (simulador)", " ".join(args[1:]))
    elif cmd == "pago" and args:
        r = svc.confirm_payment(args[0], "Admin (simulador)")
    elif cmd == "sumar" and len(args) == 2 and to_number(args[1]) is not None:
        r = svc.add_stock(args[0], int(to_number(args[1])))
    else:
        print("  Uso: :avisos | :revision | :aprobar ID [SKU=precio] | :rechazar ID motivo | :pago ID | :sumar SKU cantidad")
        return
    print(f"  [admin] {'✔' if r.ok else '✘'} {r.message}")
    show(r.notify)


def main() -> None:
    try:
        s = load_settings(require_telegram=False)
    except ConfigError as exc:
        sys.exit(f"Error de configuración: {exc}")
    svc = BotService(build_repository(s), s)
    print(__doc__)
    while True:
        try:
            text = input("\nCLIENTE> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if text == ":salir":
            break
        if re.match(r"^:(cf|cd|cx|aj|dk|dn|pe|pt) ", text):
            show(svc.handle_callback(CLIENT, f"{text[1:3]}:{text[4:].strip()}"))
        elif text.startswith(":"):
            admin(svc, text)
        else:
            show(svc.handle_message(CLIENT, text))


if __name__ == "__main__":
    main()
