"""Фаза 0: проверка миграций и схемы данных."""

EXPECTED_TABLES = {
    "users", "admins", "schedule_templates", "schedule_exceptions",
    "blocked_slots", "categories", "services", "bookings",
    "booking_slots", "booking_services", "notifications", "schema_version",
}


async def test_migrations_create_all_tables(db):
    rows = await db.fetchall(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    )
    names = {row["name"] for row in rows}
    assert EXPECTED_TABLES <= names, f"нет таблиц: {EXPECTED_TABLES - names}"


async def test_categories_seeded(db):
    rows = await db.fetchall("SELECT name FROM categories ORDER BY sort_order")
    assert [row["name"] for row in rows] == ["Стрижка", "Борода и усы"]


async def test_schema_version_is_one(db):
    assert await db.fetchval("SELECT MAX(version) FROM schema_version") == 1


async def test_foreign_keys_enabled(db):
    assert await db.fetchval("PRAGMA foreign_keys") == 1


async def test_migrations_idempotent(db):
    await db.migrate()
    assert await db.fetchval("SELECT COUNT(*) FROM categories") == 2


async def test_active_slot_unique_index_exists(db):
    rows = await db.fetchall(
        "SELECT name FROM sqlite_master WHERE type='index' AND name='ux_active_slot'"
    )
    assert rows, "частичный уникальный индекс активных слотов не создан"
