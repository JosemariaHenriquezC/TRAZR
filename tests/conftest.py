import re

import pytest

from app.config import Rules, Settings
from app.service import BotService, User
from app.storage.excel import ExcelRepository, create_excel_files
from app.sample_data import sample_stock_rows

ADMIN = User(1001, "Admin")
CLIENT = User(2002, "Juana Pérez")


def plain(text: str) -> str:
    return re.sub(r"</?(b|i|code)>", "", text)


@pytest.fixture
def settings(tmp_path):
    return Settings(
        telegram_token="", admin_chat_ids={ADMIN.id}, storage_backend="excel",
        excel_stock_path=str(tmp_path / "stock.xlsx"), excel_orders_path=str(tmp_path / "pedidos.xlsx"),
        timezone="UTC", rules=Rules(),
    )


@pytest.fixture
def repo(settings):
    create_excel_files(settings.excel_stock_path, settings.excel_orders_path, sample_stock_rows())
    return ExcelRepository(settings.excel_stock_path, settings.excel_orders_path)


@pytest.fixture
def svc(repo, settings):
    return BotService(repo, settings)
