"""Productos de EJEMPLO para probar (reemplazalos por los tuyos en la hoja)."""
from datetime import date, timedelta


def sample_stock_rows() -> list[list]:
    in_a_year = (date.today() + timedelta(days=365)).isoformat()
    expired = (date.today() - timedelta(days=1)).isoformat()
    # SKU | Producto | Alias | Stock | Precio | Vigente_hasta | Activo | Actualizado
    return [
        ["YER1", "Yerba Mate 1kg", "yerba, mate", 50, 4500, in_a_year, "SI", ""],
        ["AZU1", "Azúcar 1kg", "azucar", 30, 1200, in_a_year, "SI", ""],
        ["ACE1", "Aceite de Girasol 1.5L", "aceite, girasol", 5, 3800, "", "SI", ""],
        ["HAR1", "Harina 000 1kg", "harina", 40, 900, in_a_year, "SI", ""],
        ["ARR1", "Arroz Largo Fino 1kg", "arroz", 25, 1500, in_a_year, "SI", ""],
        ["FID1", "Fideos Spaghetti 500g", "fideos, spaghetti, tallarines", 60, 1100, in_a_year, "SI", ""],
        ["CAF1", "Café Molido 250g", "cafe", 10, 5200, expired, "SI", ""],   # precio vencido (prueba)
        ["GAL1", "Galletitas Dulces 300g", "galletitas dulces", 20, 950, in_a_year, "SI", ""],
        ["GAL2", "Galletitas Saladas 300g", "galletitas saladas, crackers", 20, 980, in_a_year, "SI", ""],
        ["LEC1", "Leche Entera 1L", "leche", 0, 1300, in_a_year, "SI", ""],      # sin stock (prueba)
    ]
