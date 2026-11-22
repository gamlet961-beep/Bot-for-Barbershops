"""Фаза 1: роли и доступ (раздел 3 ТЗ; A.1.1–A.1.4 акта)."""

from bot.db.database import Database
from bot.db.repositories.admins import AdminsRepo
from bot.db.repositories.users import UsersRepo

PRIMARY_TG = 111
SECOND_TG = 222
STRANGER_TG = 333


async def _setup(db: Database) -> AdminsRepo:
    repo = AdminsRepo(db)
    await repo.ensure_primary(PRIMARY_TG)
    return repo


async def test_ensure_primary_creates_single_admin(db):
    repo = await _setup(db)
    assert await repo.is_admin(PRIMARY_TG)
    assert await repo.is_primary(PRIMARY_TG)
    # повторная инициализация не плодит записи
    await repo.ensure_primary(PRIMARY_TG)
    admins = await repo.list_admins()
    assert len(admins) == 1
    assert admins[0]["telegram_id"] == PRIMARY_TG


async def test_ensure_primary_resets_old_primary(db):
    repo = await _setup(db)
    await repo.ensure_primary(SECOND_TG)  # смена основного в конфигурации
    assert await repo.is_primary(SECOND_TG)
    assert not await repo.is_primary(PRIMARY_TG)
    assert await repo.is_admin(PRIMARY_TG)  # прежний остаётся админом


async def test_add_second_admin(db):
    repo = await _setup(db)
    assert await repo.add_admin(SECOND_TG, added_by=PRIMARY_TG)
    assert await repo.is_admin(SECOND_TG)
    assert not await repo.is_primary(SECOND_TG)
    # повторное добавление отклоняется (A.1.2 — без дублей)
    assert not await repo.add_admin(SECOND_TG, added_by=PRIMARY_TG)


async def test_remove_second_admin_and_protect_primary(db):
    repo = await _setup(db)
    await repo.add_admin(SECOND_TG, added_by=PRIMARY_TG)
    # основного удалить нельзя (A.1.3)
    assert not await repo.remove_admin(PRIMARY_TG)
    # второго можно
    assert await repo.remove_admin(SECOND_TG)
    assert not await repo.is_admin(SECOND_TG)
    # повторное удаление — False
    assert not await repo.remove_admin(SECOND_TG)


async def test_stranger_is_not_admin(db):
    repo = await _setup(db)
    assert not await repo.is_admin(STRANGER_TG)
    assert not await repo.is_primary(STRANGER_TG)


async def test_admin_tg_ids_for_fanout(db):
    repo = await _setup(db)
    await repo.add_admin(SECOND_TG, added_by=PRIMARY_TG)
    assert sorted(await repo.admin_tg_ids()) == [PRIMARY_TG, SECOND_TG]


async def test_users_upsert_stable_id(db):
    repo = UsersRepo(db)
    user_id = await repo.upsert_by_telegram(STRANGER_TG, "old_name")
    same_id = await repo.upsert_by_telegram(STRANGER_TG, "new_name")
    assert user_id == same_id
    row = await repo.get_by_telegram_id(STRANGER_TG)
    assert row["username"] == "new_name"
    total = await repo.get_by_telegram_id(STRANGER_TG)
    assert total is not None
    count = await db.fetchval("SELECT COUNT(*) FROM users")
    assert count == 1


async def test_users_set_contact(db):
    repo = UsersRepo(db)
    user_id = await repo.upsert_by_telegram(STRANGER_TG, "user")
    await repo.set_contact(user_id, "Иван", "+79990001122")
    row = await repo.get_by_phone("+79990001122")
    assert row is not None and row["name"] == "Иван"
