"""
Usuarios de la web de administración.

    python -m scripts.crear_admin lucia "Lucía Fernández"   # crear, o cambiar nombre y contraseña
    python -m scripts.crear_admin --listar                  # ver los usuarios (nunca las contraseñas)
    python -m scripts.crear_admin --desactivar lucia        # dar de baja (no puede ingresar; no se borra)
    python -m scripts.crear_admin --activar lucia           # volver a dar de alta

Al crear o cambiar, pide la contraseña sin mostrarla (mínimo 8 caracteres). Se guarda con
hash scrypt: nunca en texto plano, y no se puede ver. Para cambiarla, se vuelve a crear con
el mismo usuario.
"""
from __future__ import annotations

import argparse
import getpass
import sys

from app.config import ConfigError, load_settings
from app.service import BotService
from app.storage import build_repository


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("usuario", nargs="?", help="usuario para ingresar (ej. lucia)")
    ap.add_argument("nombre", nargs="?", help="nombre que se muestra (ej. \"Lucía Fernández\")")
    group = ap.add_mutually_exclusive_group()
    group.add_argument("--listar", action="store_true", help="mostrar los usuarios")
    group.add_argument("--desactivar", metavar="USUARIO", help="dar de baja a un usuario")
    group.add_argument("--activar", metavar="USUARIO", help="volver a dar de alta a un usuario")
    args = ap.parse_args()
    try:
        s = load_settings(require_telegram=False)
    except ConfigError as exc:
        sys.exit(f"Error de configuración: {exc}")
    svc = BotService(build_repository(s), s)

    if args.listar:
        admins = svc.repo.list_admins()
        if not admins:
            print("Todavía no hay usuarios. Creá uno con: python -m scripts.crear_admin usuario \"Nombre\"")
            return
        print(f"{'USUARIO':<16} {'NOMBRE':<30} ESTADO")
        for a in admins:
            print(f"{a.username:<16} {a.name:<30} {'activo' if a.active else 'dado de baja'}")
        return

    if args.desactivar or args.activar:
        username = (args.desactivar or args.activar).strip().lower()
        admin = svc.repo.get_admin(username)
        if admin is None:
            sys.exit(f"No existe el usuario «{username}». Mirá los que hay con --listar.")
        admin.active = bool(args.activar)
        if not admin.active and sum(1 for a in svc.repo.list_admins() if a.active) <= 1:
            sys.exit("Es el último usuario activo: si lo das de baja nadie puede entrar a la web.")
        svc.repo.save_admin(admin, svc.stamp())
        print(f"Usuario «{username}» {'activado' if admin.active else 'dado de baja: ya no puede ingresar'}.")
        return

    if not args.usuario or not args.nombre:
        ap.error("para crear o cambiar un usuario: python -m scripts.crear_admin usuario \"Nombre\"")
    password = getpass.getpass("Contraseña: ")
    if password != getpass.getpass("Repetila: "):
        sys.exit("Las contraseñas no coinciden.")
    result = svc.create_admin(args.usuario, args.nombre, password)
    if not result.ok:
        sys.exit(result.message)
    print(result.message)


if __name__ == "__main__":
    main()
