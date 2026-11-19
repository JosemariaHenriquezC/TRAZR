"""
Cómo le va al bot con los clientes reales, a partir de los eventos que registra
(nunca guarda el texto de los mensajes).

    python -m scripts.metricas            # últimos 7 días
    python -m scripts.metricas --dias 30
"""
from __future__ import annotations

import argparse
import statistics
import sys
from collections import Counter
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.config import ConfigError, load_settings
from app.storage import build_repository


def _p95(values: list[int]) -> int:
    values = sorted(values)
    return values[min(len(values) - 1, int(len(values) * 0.95))]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dias", type=int, default=7, help="cuántos días mirar (por defecto 7)")
    args = ap.parse_args()
    try:
        s = load_settings(require_telegram=False)
    except ConfigError as exc:
        sys.exit(f"Error de configuración: {exc}")
    repo = build_repository(s)
    since = (datetime.now(ZoneInfo(s.timezone)) - timedelta(days=args.dias)).strftime("%Y-%m-%d %H:%M:%S")
    events = repo.list_events(since)
    if not events:
        print(f"No hay eventos en los últimos {args.dias} días.")
        return

    count = Counter(e["tipo"] for e in events)
    clientes = {(e["canal"], e["cliente_id"]) for e in events if e["tipo"] == "mensaje"}
    print(f"Últimos {args.dias} días · {len(clientes)} clientes escribieron\n")
    print(f"Mensajes recibidos        {count['mensaje']}")
    print(f"Botones tocados           {count['boton']}")
    print(f"Resúmenes mostrados       {count['resumen']}")
    print(f"Pedidos registrados       {count['pedido_registrado']}")
    print(f"Pedidos descartados       {count['pedido_cancelado']}")
    print(f"Pedidos de aclaración     {count['aclaracion']}")
    if count["resumen"]:
        print(f"\nDe cada resumen, terminó en pedido: {count['pedido_registrado'] / count['resumen']:.0%}")
    if count["pedido_registrado"]:
        turnos = (count["mensaje"] + count["boton"]) / count["pedido_registrado"]
        print(f"Mensajes y botones por pedido:      {turnos:.1f}")

    resp = [e["latencia_ms"] for e in events if e["tipo"] == "mensaje" and e["latencia_ms"] is not None]
    ia = [e["latencia_ms"] for e in events if e["tipo"] == "ia" and e["latencia_ms"] is not None]
    if resp:
        print(f"\nTiempo de respuesta: mediana {statistics.median(resp) / 1000:.1f} s, "
              f"95 % por debajo de {_p95(resp) / 1000:.1f} s")
    if ia:
        sin = sum(1 for e in events if e["tipo"] == "ia" and e["detalle"] == "sin_respuesta")
        print(f"Gemini: {len(ia)} consultas, mediana {statistics.median(ia) / 1000:.1f} s, "
              f"{sin} sin respuesta, {count['ia_caida']} veces se le pidió al cliente que escriba más tarde")
    motivos = Counter(m for e in events if e["tipo"] == "aclaracion" for m in (e["detalle"] or "").split(",") if m)
    if motivos:
        nombres = {"ambiguo": "producto ambiguo", "sin_cantidad": "falta la cantidad", "bulto": "cajas o packs",
                   "no_existe": "producto que no hay", "cantidad_invalida": "cantidad inválida",
                   "sin_producto": "no se entendió el producto"}
        print("\nPor qué se pidió aclaración:")
        for m, n in motivos.most_common():
            print(f"  {nombres.get(m, m):<28} {n}")


if __name__ == "__main__":
    main()
