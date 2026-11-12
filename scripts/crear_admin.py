"""
Crea (o actualiza) un usuario de la web de administración.

    python -m scripts.crear_admin lucia "Lucía Fernández"

Pide la contraseña sin mostrarla (mínimo 8 caracteres). Se guarda con hash scrypt:
nunca en texto plano. Si el usuario ya existe, le cambia el nombre y la contraseña.
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
    ap.add_argument("usuario", help="usuario para ingresar (ej. lucia)")
    ap.add_argument("nombre", help="nombre que se muestra (ej. \"Lucía Fernández\")")
    args = ap.parse_args()
    try:
        s = load_settings(require_telegram=False)
    except ConfigError as exc:
        sys.exit(f"Error de configuración: {exc}")
    password = getpass.getpass("Contraseña: ")
    if password != getpass.getpass("Repetila: "):
        sys.exit("Las contraseñas no coinciden.")
    result = BotService(build_repository(s), s).create_admin(args.usuario, args.nombre, password)
    if not result.ok:
        sys.exit(result.message)
    print(result.message)


if __name__ == "__main__":
    main()
