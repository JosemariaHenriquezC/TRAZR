"""
Prepara las dos hojas de cálculo: crea la pestaña si no existe, escribe el
encabezado (fila 1) y, si se pide, carga productos de ejemplo en el stock.

Uso:
    python -m scripts.init_sheets            # solo encabezados (no borra datos)
    python -m scripts.init_sheets --ejemplo  # + productos de ejemplo si el stock está vacío

Funciona con el backend configurado en .env (gsheets o excel).
"""
from __future__ import annotations

import argparse
import sys

from app.config import ConfigError, load_settings
from app.models import ORDER_COLUMNS, STOCK_COLUMNS
from app.sample_data import sample_stock_rows


def init_gsheets(s, with_sample: bool) -> None:
    import gspread
    from gspread.utils import ValueInputOption

    client = gspread.service_account(filename=s.google_credentials_file)
    creds = getattr(getattr(client, "http_client", None), "auth", None)
    print(f"Cuenta de servicio: {getattr(creds, 'service_account_email', '(desconocida)')}")
    for sid, tab, header, sample in [
        (s.stock_spreadsheet_id, s.stock_worksheet, STOCK_COLUMNS, sample_stock_rows() if with_sample else []),
        (s.orders_spreadsheet_id, s.orders_worksheet, ORDER_COLUMNS, []),
    ]:
        sh = client.open_by_key(sid)
        try:
            ws = sh.worksheet(tab)
        except gspread.exceptions.WorksheetNotFound:
            ws = sh.add_worksheet(title=tab, rows=1000, cols=len(header))
            print(f"  + pestaña '{tab}' creada en «{sh.title}»")
        first = ws.row_values(1)
        if first and [c.strip() for c in first[:len(header)]] != header:
            print(f"  ! «{sh.title}» / '{tab}' ya tiene otro encabezado: {first}. No lo toco; "
                  f"corregilo a mano: {' | '.join(header)}")
            continue
        if not first:
            ws.update([header], "A1", value_input_option=ValueInputOption.raw)
            ws.freeze(rows=1)
            print(f"  ✓ encabezado escrito en «{sh.title}» / '{tab}'")
        if sample and len(ws.get_all_values()) <= 1:
            ws.append_rows(sample, value_input_option=ValueInputOption.user_entered)
            print(f"  ✓ {len(sample)} productos de ejemplo cargados")
    print("Listo.")


def init_excel(s, with_sample: bool) -> None:
    from app.storage.excel import create_excel_files
    create_excel_files(s.excel_stock_path, s.excel_orders_path,
                       sample_stock_rows() if with_sample else [],
                       s.stock_worksheet, s.orders_worksheet)
    print(f"Listo: {s.excel_stock_path} y {s.excel_orders_path} (si ya existían, no se tocaron).")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ejemplo", action="store_true", help="cargar productos de ejemplo en el stock")
    args = ap.parse_args()
    try:
        s = load_settings(require_telegram=False)
    except ConfigError as exc:
        sys.exit(f"Error de configuración: {exc}")
    try:
        (init_gsheets if s.storage_backend == "gsheets" else init_excel)(s, args.ejemplo)
    except Exception as exc:  # mensaje claro para el usuario
        sys.exit(f"No se pudo inicializar ({exc.__class__.__name__}): {exc}\n"
                 "Revisá los IDs de las hojas y que estén compartidas con la cuenta de servicio.")


if __name__ == "__main__":
    main()
