"""Fábrica del repositorio según STORAGE_BACKEND."""
from ..config import Settings
from .base import Repository, StorageError


def build_repository(settings: Settings) -> Repository:
    if settings.storage_backend == "excel":
        from .excel import ExcelRepository
        return ExcelRepository(settings.excel_stock_path, settings.excel_orders_path,
                               settings.stock_worksheet, settings.orders_worksheet)
    from .gsheets import GoogleSheetsRepository
    return GoogleSheetsRepository(settings.google_credentials_file,
                                  settings.stock_spreadsheet_id, settings.orders_spreadsheet_id,
                                  settings.stock_worksheet, settings.orders_worksheet)


__all__ = ["build_repository", "Repository", "StorageError"]
