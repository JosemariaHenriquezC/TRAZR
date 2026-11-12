"""Catálogo de EJEMPLO de bebidas para probar (reemplazalo por el tuyo)."""
from datetime import date, timedelta

from .models import Product


def sample_products() -> list[Product]:
    in_a_year = date.today() + timedelta(days=365)
    expired = date.today() - timedelta(days=2)  # 2 días: vencido en cualquier zona horaria

    def p(sku, name, category, aliases, stock, price, valid_until=in_a_year):
        return Product(sku, name, aliases, stock, price, valid_until, True, category)

    return [
        p("COC1", "Coca-Cola 2.25L", "Gaseosas", ["coca", "coca cola", "cocacola"], 50, 3200),
        p("SPR1", "Sprite 2.25L", "Gaseosas", ["sprite", "lima limon"], 40, 3000),
        p("FAN1", "Fanta Naranja 2.25L", "Gaseosas", ["fanta"], 40, 3000),
        p("AGU1", "Agua Mineral sin Gas 2L", "Aguas", ["agua sin gas"], 30, 1100),
        p("AGU2", "Agua Mineral con Gas 2L", "Aguas", ["agua con gas"], 30, 1100),
        p("CER1", "Cerveza Rubia 1L", "Cervezas", ["cerveza", "birra"], 5, 2800, None),
        p("VIN1", "Vino Tinto Malbec 750ml", "Vinos", ["vino", "malbec", "tinto"], 0, 6500),   # sin stock (prueba)
        p("FER1", "Fernet 750ml", "Aperitivos", ["fernet"], 10, 12000, expired),  # precio vencido (prueba)
        p("JUG1", "Jugo de Naranja 1L", "Jugos", ["jugo", "jugo de naranja"], 25, 1500),
        p("ENE1", "Bebida Energizante 473ml", "Energizantes", ["energizante", "energetica"], 60, 1800),
    ]
