"""Фаза 3: сервис записей — транзакции, отмены, уведомления (п. 6.2, 4.2 ТЗ).

Notificator подменяется записывающим, чтобы проверять состав сообщений
без Telegram (логика A.5.10–A.5.11, A.2.7, A.4.6).
"""

import asyncio
from datetime import date, time, timedelta

import pytest

from bot.db.repositories.admins import AdminsRepo
from bot.db.repositories.bookings import BookingsRepo
from bot.db.repositories.notifications import NotificationsRepo
from bot.db.repositories.schedule import ScheduleRepo
from bot.db.repositories.users import UsersRepo
from bot.services.booking import BookingService, DayOffError, SlotTakenError
from bot.services.notifications import Notificator
from bot.services.scheduling import DayRule

PRIMARY_TG = 111
SECOND_TG = 222
CLIENT_TG = 333

# Понедельник через 1–2 недели: окна напоминаний (24 ч) всегда в будущем,
# набор тестов не зависит от текущей даты при запуске.
_TODAY = date.today()
MONDAY = _TODAY + timedelta(days=((7 - _TODAY.weekday()) % 7 or 7) + 7)
T = time


class RecordingNotificator(Notificator):
    """Фиксирует все отправки: (chat_id, text)."""

    def __init__(self, db) -> None:
        super().__init__(None, db)
        self.sent: list[tuple[int, str]] = []

    async def send_message(self, chat_id, text):  # type: ignore[override]
        self.sent.append((chat_id, text))
        return "sent"


async def seed(db, *, working_monday: bool = True) -> RecordingNotificator:
    """Минимальный мир: админы, клиент, услуги, шаблон пн 10:00–20:00."""
    admins = AdminsRepo(db)
    await admins.ensure_primary(PRIMARY_TG)
    await admins.add_admin(SECOND_TG, added_by=PRIMARY_TG)
    users = UsersRepo(db)
    client_user_id = await users.upsert_by_telegram(CLIENT_TG, "client")
    await users.set_contact(client_user_id, "Иван", "+79260001122")
    if working_monday:
        async with db.transaction():
            await ScheduleRepo(db).upsert_template(0, DayRule.work(T(10, 0), T(20, 0)))
    async with db.transaction():
        for sid, name, price in [(1, "Стрижка", 2000), (2, "Борода", 1000)]:
            await db.execute(
                "INSERT INTO services (id, category_id, name, description, price, is_active, sort_order) "
                "VALUES (?, 1, ?, NULL, ?, 1, ?) ON CONFLICT(id) DO NOTHING",
                (sid, name, price, sid),
            )
    return RecordingNotificator(db)


def client_dict(db_user_id: int) -> dict:
    return {
        "user_id": db_user_id,
        "tg_id": CLIENT_TG,
        "name": "Иван",
        "phone": "+79260001122",
    }


def service_rows() -> list[dict]:
    return [{"id": 1, "name": "Стрижка", "price": 2000},
            {"id": 2, "name": "Борода", "price": 1000}]


async def client_user_id(db) -> int:
    row = await db.fetchone("SELECT id FROM users WHERE telegram_id = ?", (CLIENT_TG,))
    return row["id"]


# ── создание записи ──

async def test_create_booking_snapshot_and_plans(db):
    notificator = await seed(db)
    service = BookingService(db, notificator)
    booking_id = await service.create_booking(
        service_rows=service_rows(),
        day=MONDAY,
        slot=(T(18, 0), T(20, 0)),
        client=client_dict(await client_user_id(db)),
        created_by="client",
        pd_consent=True,
    )
    repo = BookingsRepo(db)
    row = await repo.get(booking_id)
    assert row["status"] == "active"
    assert row["total_price"] == 3000
    assert row["pd_consent_at"] is not None
    assert row["slot_start"] == "18:00" and row["slot_end"] == "20:00"
    services = await db.fetchall(
        "SELECT name_at_booking, price_at_booking FROM booking_services WHERE booking_id = ?",
        (booking_id,),
    )
    assert [s["name_at_booking"] for s in services] == ["Стрижка", "Борода"]
    # Планы напоминаний: 24 ч и 2 ч клиенту (pending), 1 ч админам
    plans = {p["type"]: p for p in await NotificationsRepo(db).booking_plans(booking_id)}
    assert plans["client_24h"]["status"] == "pending"
    assert plans["client_2h"]["recipient_tg_id"] == CLIENT_TG
    assert plans["admin_1h"]["recipient_tg_id"] == 0
    # Мгновенные сообщения: подтверждение клиенту + уведомление двум админам
    chats = [chat for chat, _ in notificator.sent]
    assert chats == [CLIENT_TG, PRIMARY_TG, SECOND_TG]
    assert "Вы успешно записаны!" in notificator.sent[0][1]
    assert "Новая запись клиента" in notificator.sent[1][1]
    assert "Иван" in notificator.sent[1][1] and "+79260001122" in notificator.sent[1][1]


async def test_create_on_day_off_raises(db):
    await seed(db, working_monday=False)
    service = BookingService(db, None)
    with pytest.raises(DayOffError):
        await service.create_booking(
            service_rows=[service_rows()[0]],
            day=MONDAY,
            slot=(T(10, 0), T(12, 0)),
            client=client_dict(await client_user_id(db)),
            created_by="client",
            pd_consent=True,
        )


async def test_double_booking_rejected_sequentially(db):
    await seed(db)
    service = BookingService(db, None)
    client = client_dict(await client_user_id(db))
    await service.create_booking(
        service_rows=[service_rows()[0]], day=MONDAY, slot=(T(10, 0), T(12, 0)),
        client=client, created_by="client", pd_consent=True,
    )
    with pytest.raises(SlotTakenError):
        await service.create_booking(
            service_rows=[service_rows()[1]], day=MONDAY, slot=(T(10, 0), T(12, 0)),
            client=client, created_by="client", pd_consent=True,
        )


async def test_parallel_booking_only_one_wins(db):
    """B.4: параллельные попытки — побеждает одна, вторая получает «слот занят»."""
    await seed(db)
    service = BookingService(db, None)
    client = client_dict(await client_user_id(db))
    results = await asyncio.gather(
        service.create_booking(
            service_rows=[service_rows()[0]], day=MONDAY, slot=(T(14, 0), T(16, 0)),
            client=client, created_by="client", pd_consent=True,
        ),
        service.create_booking(
            service_rows=[service_rows()[1]], day=MONDAY, slot=(T(14, 0), T(16, 0)),
            client=client, created_by="client", pd_consent=True,
        ),
        return_exceptions=True,
    )
    ok = [r for r in results if isinstance(r, int)]
    errors = [r for r in results if isinstance(r, Exception)]
    assert len(ok) == 1 and len(errors) == 1
    assert isinstance(errors[0], SlotTakenError)
    count = await db.fetchval("SELECT COUNT(*) FROM booking_slots "
                              "WHERE date = ? AND slot_start = '14:00'", (MONDAY.isoformat(),))
    assert count == 1


async def test_blocked_slot_rejects_booking(db):
    await seed(db)
    schedule = ScheduleRepo(db)
    async with db.transaction():
        await schedule.block_slot(MONDAY, T(12, 0), T(14, 0), reason="личное")
    service = BookingService(db, None)
    with pytest.raises(SlotTakenError):
        await service.create_booking(
            service_rows=[service_rows()[0]], day=MONDAY, slot=(T(12, 0), T(14, 0)),
            client=client_dict(await client_user_id(db)),
            created_by="client", pd_consent=True,
        )


# ── отмена записи ──

async def test_cancel_frees_slot_and_skips_plans(db):
    await seed(db)
    notificator = RecordingNotificator(db)
    service = BookingService(db, notificator)
    client = client_dict(await client_user_id(db))
    booking_id = await service.create_booking(
        service_rows=[service_rows()[0]], day=MONDAY, slot=(T(10, 0), T(12, 0)),
        client=client, created_by="client", pd_consent=True,
    )
    assert await service.cancel_booking(booking_id, initiator="client", reason="передумал")
    row = await BookingsRepo(db).get(booking_id)
    assert row["status"] == "cancelled"
    assert row["cancel_initiator"] == "client"
    slot = await db.fetchone("SELECT is_active FROM booking_slots WHERE booking_id = ?", (booking_id,))
    assert slot["is_active"] == 0
    plans = await NotificationsRepo(db).booking_plans(booking_id)
    assert all(p["status"] == "skipped" for p in plans)
    # A.7.6: админам ушло «Клиент ... отменил запись ...»
    admin_texts = [t for c, t in notificator.sent if c in (PRIMARY_TG, SECOND_TG)]
    assert any("отменил запись" in t for t in admin_texts)
    # Слот снова свободен — новая запись проходит
    second = await service.create_booking(
        service_rows=[service_rows()[0]], day=MONDAY, slot=(T(10, 0), T(12, 0)),
        client=client, created_by="client", pd_consent=True,
    )
    assert second != booking_id
    # Повторная отмена безопасна
    assert not await service.cancel_booking(booking_id, initiator="client", reason="x")


async def test_cancel_by_admin_notifies_client(db):
    await seed(db)
    notificator = RecordingNotificator(db)
    service = BookingService(db, notificator)
    client = client_dict(await client_user_id(db))
    booking_id = await service.create_booking(
        service_rows=[service_rows()[0]], day=MONDAY, slot=(T(10, 0), T(12, 0)),
        client=client, created_by="client", pd_consent=True,
    )
    notificator.sent.clear()
    assert await service.cancel_booking(booking_id, initiator="admin", reason="неявка")
    client_texts = [t for c, t in notificator.sent if c == CLIENT_TG]
    assert client_texts and "отменена администратором" in client_texts[0]


# ── ручная запись админом (п. 4.5, 7.1 ТЗ) ──

async def test_manual_booking_no_instant_notifications(db):
    notificator = await seed(db)
    service = BookingService(db, notificator)
    booking_id = await service.create_booking(
        service_rows=[service_rows()[0]], day=MONDAY, slot=(T(12, 0), T(14, 0)),
        client=None,  # без совпадения по телефону
        created_by="admin",
        pd_consent=False,
    )
    assert not notificator.sent  # A.4.6: ни админам, ни клиенту
    plans = {p["type"]: p for p in await NotificationsRepo(db).booking_plans(booking_id)}
    assert plans["client_24h"]["status"] == "skipped"
    assert plans["client_2h"]["status"] == "skipped"
    assert plans["admin_1h"]["status"] == "pending"  # A.4.7
    row = await BookingsRepo(db).get(booking_id)
    assert row["created_by"] == "admin" and row["pd_consent_at"] is None


async def test_manual_booking_links_client_by_phone(db):
    await seed(db)
    notificator = RecordingNotificator(db)
    service = BookingService(db, notificator)
    # Ручная запись с телефоном уже зарегистрированного клиента
    booking_id = await service.create_booking(
        service_rows=[service_rows()[0]], day=MONDAY, slot=(T(12, 0), T(14, 0)),
        client=client_dict(await client_user_id(db)),
        created_by="admin",
        pd_consent=False,
    )
    assert not notificator.sent  # мгновенных всё ещё нет
    plans = {p["type"]: p for p in await NotificationsRepo(db).booking_plans(booking_id)}
    # П. 7.1 ТЗ: ручная запись порождает напоминания клиенту 24/2 ч и админу 1 ч
    assert plans["client_24h"]["status"] == "pending"
    assert plans["client_2h"]["status"] == "pending"
    assert plans["admin_1h"]["status"] == "pending"


# ── удаление рабочего дня (UC-8, A.2.6–A.2.8) ──

async def test_cancel_working_day_full_flow(db):
    notificator = await seed(db)
    service = BookingService(db, notificator)
    client = client_dict(await client_user_id(db))
    first = await service.create_booking(
        service_rows=[service_rows()[0]], day=MONDAY, slot=(T(10, 0), T(12, 0)),
        client=client, created_by="client", pd_consent=True,
    )
    second = await service.create_booking(
        service_rows=[service_rows()[1:], ][0], day=MONDAY, slot=(T(18, 0), T(20, 0)),
        client=client, created_by="client", pd_consent=True,
    )
    notificator.sent.clear()
    views = await service.cancel_working_day(MONDAY)
    assert len(views) == 2
    # обе записи отменены с причиной и инициатором auto_day
    for booking_id in (first, second):
        row = await BookingsRepo(db).get(booking_id)
        assert row["status"] == "cancelled"
        assert row["cancel_reason"] == "рабочий день отменён"
        assert row["cancel_initiator"] == "auto_day"
        slot = await db.fetchone("SELECT is_active FROM booking_slots WHERE booking_id = ?", (booking_id,))
        assert slot["is_active"] == 0
    # исключение «день отменён» создано
    exception = await ScheduleRepo(db).get_exception(MONDAY)
    assert exception is not None and exception["is_working_day"] == 0
    # клиентам — уведомление об отмене, админам — отчёт
    client_texts = [t for c, t in notificator.sent if c == CLIENT_TG]
    admin_texts = [t for c, t in notificator.sent if c in (PRIMARY_TG, SECOND_TG)]
    assert len(client_texts) == 2 and all("рабочий день отменён" in t for t in client_texts)
    assert len(admin_texts) == 2 and all("Отчёт" in t for t in admin_texts)
    # слоты свободны — день больше не рабочий, бронь невозможна
    with pytest.raises(DayOffError):
        await service.create_booking(
            service_rows=[service_rows()[0]], day=MONDAY, slot=(T(14, 0), T(16, 0)),
            client=client, created_by="client", pd_consent=True,
        )
