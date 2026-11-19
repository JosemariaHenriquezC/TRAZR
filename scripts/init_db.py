"""
Crea la base de datos SQLite (si no existe) y carga el catálogo de bebidas.

Uso:
    python -m scripts.init_db                       # solo crea las tablas
    python -m scripts.init_db --ejemplo             # + bebidas de ejemplo (si el catálogo está vacío)
    python -m scripts.init_db --csv catalogo.csv    # alta/actualización de productos desde un CSV
    python -m scripts.init_db --csv catalogo/mayorista_tucuman.csv --reemplazar
        # el CSV pasa a ser TODO el catálogo: lo que no está en él queda inactivo (no se borra)

El CSV lleva encabezado en la primera fila con estas columnas (el orden no importa;
separador coma o punto y coma, así lo podés exportar desde Excel o Google Sheets):
    SKU, Producto, Categoria, Alias, Stock, Precio, Activo
- Alias: otras formas de nombrarlo, separadas por coma (poné la celda entre comillas).
- Activo: SI / NO (vacío = SI).
Columnas opcionales para vender por caja (pack, fardo, cajón):
    UnidadesPorCaja (ej. 6; vacío o 0 = no se vende por caja), NombreCaja (ej. pack; vacío = caja)
Si un SKU ya existe, se reemplazan sus datos. Nunca se borra nada: con --reemplazar, los
productos que no están en el CSV quedan inactivos (los pedidos viejos los siguen mostrando).
"""
from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from app.config import ConfigError, load_settings
from app.models import Product
from app.sample_data import sample_products
from app.storage import SQLiteRepository, StorageError
from app.storage.base import to_bool, to_number

CSV_COLUMNS = ["SKU", "Producto", "Categoria", "Alias", "Stock", "Precio", "Activo"]


def read_csv(path: str) -> list[Product]:
    raw = Path(path).read_text(encoding="utf-8-sig")
    dialect = csv.Sniffer().sniff(raw.splitlines()[0], delimiters=",;")
    rows = list(csv.DictReader(raw.splitlines(), dialect=dialect))
    if not rows:
        raise ValueError("el CSV no tiene filas de productos")
    header = [h.strip() for h in rows[0].keys()]
    missing = [c for c in CSV_COLUMNS if c not in header]
    if missing:
        raise ValueError(f"faltan columnas: {', '.join(missing)} (esperadas: {', '.join(CSV_COLUMNS)})")
    products, errors = [], []
    for n, r in enumerate(rows, start=2):
        r = {k.strip(): (v or "").strip() for k, v in r.items() if k}
        if not r["SKU"] and not r["Producto"]:
            continue  # fila vacía
        stock, price = to_number(r["Stock"]), to_number(r["Precio"])
        pack = to_number(r.get("UnidadesPorCaja", "")) if r.get("UnidadesPorCaja") else 0
        if not r["SKU"] or not r["Producto"]:
            errors.append(f"fila {n}: falta SKU o Producto")
        elif stock is None or stock < 0 or not float(stock).is_integer():
            errors.append(f"fila {n}: Stock debe ser un entero >= 0")
        elif price is None or price < 0:
            errors.append(f"fila {n}: Precio inválido")
        elif pack is None or pack < 0 or not float(pack).is_integer():
            errors.append(f"fila {n}: UnidadesPorCaja debe ser un entero (o vacío)")
        else:
            products.append(Product(
                sku=r["SKU"].upper(), name=r["Producto"],
                aliases=[a.strip() for a in r["Alias"].split(",") if a.strip()],
                stock=int(stock), price=round(price, 2),
                active=to_bool(r["Activo"], default=True), category=r["Categoria"],
                pack_size=int(pack), pack_name=(r.get("NombreCaja") or "caja").lower()))
    if errors:
        raise ValueError("revisá el CSV:\n  " + "\n  ".join(errors))
    return products


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ejemplo", action="store_true", help="cargar bebidas de ejemplo si el catálogo está vacío")
    ap.add_argument("--csv", metavar="ARCHIVO", help="cargar o actualizar productos desde un CSV")
    ap.add_argument("--reemplazar", action="store_true",
                    help="con --csv: desactivar los productos que no están en el CSV")
    args = ap.parse_args()
    try:
        s = load_settings(require_telegram=False)
    except ConfigError as exc:
        sys.exit(f"Error de configuración: {exc}")
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        repo = SQLiteRepository(s.database_path)
        print(f"Base de datos lista: {s.database_path}")
        to_load: list[Product] = []
        if args.ejemplo:
            if repo.list_products():
                print("  · el catálogo ya tiene productos: no cargo los de ejemplo")
            else:
                to_load += sample_products()
        if args.csv:
            to_load += read_csv(args.csv)
        if args.reemplazar and not args.csv:
            raise ValueError("--reemplazar va junto con --csv")
        for p in to_load:
            repo.upsert_product(p, stamp)
        if to_load:
            print(f"  ✓ {len(to_load)} productos cargados/actualizados")
        if args.reemplazar:
            keep = {p.sku for p in to_load}
            old = [p for p in repo.list_products() if p.sku not in keep and p.active]
            for p in old:
                repo.upsert_product(replace(p, active=False), stamp)
            if old:
                print(f"  · {len(old)} productos que no están en el CSV quedaron inactivos: "
                      + ", ".join(p.sku for p in old))
        print(f"  Catálogo: {len(repo.list_products())} productos.")
    except (StorageError, ValueError, OSError) as exc:
        sys.exit(f"No se pudo inicializar: {exc}")


if __name__ == "__main__":
    main()
