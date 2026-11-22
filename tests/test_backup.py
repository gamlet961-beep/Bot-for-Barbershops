"""Фаза 9: бэкапы SQLite (B.9 акта) — снапшот, выгрузка, восстановление."""

from datetime import date, time, timedelta

from bot.db.database import Database
from bot.db.repositories.schedule import ScheduleRepo
from bot.db.repositories.users import UsersRepo
from bot.services.backup import BackupService
from bot.services.booking import BookingService
from bot.services.scheduling import DayRule
from bot.utils.dates import today_msk

CLIENT_TG = 333


class FakeGitHubBackup(BackupService):
    """Транспорт GitHub подменён словарём — сеть не используется."""

    def __init__(self, db: Database) -> None:
        super().__init__(db, repo="owner/repo", token="t", branch="main")
        self.remote: dict[str, bytes] = {}
        self.sha_counter = 0

    async def _remote_sha(self):  # noqa: D102
        if self.remote_path in self.remote:
            self.sha_counter += 1
            return f"sha-{self.sha_counter}"
        return None

    async def _put_remote(self, data, sha):  # noqa: D102
        self.remote[self.remote_path] = data

    async def _download_remote(self):  # noqa: D102
        return self.remote.get(self.remote_path)


async def _seed_booking(db: Database) -> int:
    users = UsersRepo(db)
    user_id = await users.upsert_by_telegram(CLIENT_TG, "client")
    async with db.transaction():
        await ScheduleRepo(db).upsert_template(
            (today_msk() + timedelta(days=1)).weekday(),
            DayRule.work(time(10, 0), time(20, 0)),
        )
        await db.execute(
            "INSERT INTO services (id, category_id, name, price, is_active, sort_order) "
            "VALUES (1, 1, 'Стрижка', 2000, 1, 1)"
        )
    return await BookingService(db, None).create_booking(
        service_rows=[{"id": 1, "name": "Стрижка", "price": 2000}],
        day=today_msk() + timedelta(days=1),
        slot=(time(18, 0), time(20, 0)),
        client={"user_id": user_id, "tg_id": CLIENT_TG, "name": "Иван", "phone": "+79000000000"},
        created_by="client", pd_consent=True,
    )


async def test_backup_disabled_without_config(db):
    service = BackupService(db, repo="", token="")
    assert not service.enabled()
    assert await service.backup() == "disabled"


async def test_backup_snapshot_contains_data(db):
    booking_id = await _seed_booking(db)
    backup = FakeGitHubBackup(db)
    assert await backup.backup() == "uploaded"
    snapshot = backup.remote[backup.remote_path]
    assert len(snapshot) > 0
    # Снапшот — валидная база SQLite с нашей записью
    import tempfile
    from pathlib import Path

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        tmp.write(snapshot)
        tmp_path = tmp.name
    try:
        check = Database(tmp_path)
        await check.connect()
        await check.migrate()
        count = await check.fetchval("SELECT COUNT(*) FROM bookings")
        assert count == 1
        row = await check.fetchone("SELECT id FROM bookings WHERE id = ?", (booking_id,))
        assert row is not None
        await check.close()
    finally:
        Path(tmp_path).unlink(missing_ok=True)


async def test_restore_roundtrip(tmp_path):
    """B.9: чистый инстанс восстанавливает базу из удалённого бэкапа."""
    source_path = str(tmp_path / "source.db")
    source = Database(source_path)
    await source.connect()
    await source.migrate()
    await _seed_booking(source)
    backup = FakeGitHubBackup(source)
    assert await backup.backup() == "uploaded"
    await source.close()

    target_path = str(tmp_path / "restored.db")
    target = Database(target_path)
    await target.connect()
    await target.migrate()
    restoring = FakeGitHubBackup(target)
    restoring.remote = backup.remote  # «удалённый» бэкап доступен
    assert await restoring.is_database_empty()
    await target.close()
    assert await restoring.restore_to_file(target_path)
    target = Database(target_path)
    await target.connect()
    await target.migrate()
    restored_backup = FakeGitHubBackup(target)
    assert not await restored_backup.is_database_empty()
    count = await target.fetchval("SELECT COUNT(*) FROM bookings")
    assert count == 1
    await target.close()


async def test_restore_missing_backup_returns_false(tmp_path):
    db = Database(":memory:")
    await db.connect()
    await db.migrate()
    backup = FakeGitHubBackup(db)
    backup.remote = {}  # удалённого бэкапа нет
    assert not await backup.restore_to_file(str(tmp_path / "x.db"))
    await db.close()
