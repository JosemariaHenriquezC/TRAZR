"""Catálogo de EJEMPLO de bebidas para probar (reemplazalo por el tuyo)."""
from .models import Product


def sample_products() -> list[Product]:
    def p(sku, name, category, aliases, stock, price, pack=0, pack_name="caja"):
        return Product(sku, name, aliases, stock, price, True, category, pack, pack_name)

    return [
        p("COC1", "Coca-Cola 2.25L", "Sin Alcohol", ["coca", "coca cola", "cocacola"], 50, 3200, 6, "pack"),
        p("SPR1", "Sprite 2.25L", "Sin Alcohol", ["sprite", "lima limon"], 40, 3000),                  # sin caja cargada (prueba)
        p("FAN1", "Fanta Naranja 2.25L", "Sin Alcohol", ["fanta"], 40, 3000, 6, "pack"),
        p("AGU1", "Agua Mineral sin Gas 2L", "Sin Alcohol", ["agua sin gas"], 30, 1100, 6, "pack"),
        p("AGU2", "Agua Mineral con Gas 2L", "Sin Alcohol", ["agua con gas"], 30, 1100, 6, "pack"),
        p("CER1", "Cerveza Rubia 1L", "Cervezas", ["cerveza", "birra"], 5, 2800, 12, "cajón"),      # poco stock (prueba)
        p("VIN1", "Vino Tinto Malbec 750ml", "Vinos", ["vino", "malbec", "tinto"], 0, 6500, 6),     # sin stock (prueba)
        p("FER1", "Fernet 750ml", "Fernet", ["fernet"], 10, 12000, 6),
        p("JUG1", "Jugo de Naranja 1L", "Sin Alcohol", ["jugo", "jugo de naranja"], 25, 1500, 12),
        p("ENE1", "Bebida Energizante 473ml", "Energizantes", ["energizante", "energetica"], 60, 1800, 24),
    ]
