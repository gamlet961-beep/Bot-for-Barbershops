"""Общие фикстуры тестов: база в памяти с применёнными миграциями."""

import pytest

from bot.db.database import Database


@pytest.fixture
async def db():
    database = Database(":memory:")
    await database.connect()
    await database.migrate()
    yield database
    await database.close()
