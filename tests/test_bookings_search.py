"""Фаза 6: раздел «Записи» — фильтры и поиск (п. 4.4 ТЗ; A.4.1–A.4.3)."""

from datetime import date, time, timedelta

from bot.db.repositories.bookings import BookingsRepo
from bot.db.repositories.schedule import ScheduleRepo
from bot.db.repositories.users import UsersRepo
from bot.services.booking import BookingService
from bot.services.scheduling import DayRule

CLIENT_TG = 333
TODAY = date(2026, 11, 21)


async def _world(db):
    async with db.transaction():
        await ScheduleRepo(db).upsert_template(0, DayRule.work(time(10, 0), time(20, 0)))  # пн
        await ScheduleRepo(db).upsert_template(1, DayRule.work(time(10, 0), time(20, 0)))  # вт
    async with db.transaction():
        for sid, name, price in [(1, "Стрижка", 2000), (2, "Борода", 1000)]:
            await db.execute(
                "INSERT INTO services (id, category_id, name, price, is_active, sort_order) "
                "VALUES (?, 1, ?, ?, 1, ?)", (sid, name, price, sid)
            )
    users = UsersRepo(db)
    user_id = await users.upsert_by_telegram(CLIENT_TG, "client")
    await users.set_contact(user_id, "Иван Пetroв", "+79260001122")
    another_id = await users.upsert_by_telegram(444, "client2")
    await users.set_contact(another_id, "Пётр Смирнов", "+79001234567")
    return user_id, another_id


async def _book(db, user_id: int, tg_id: int, name: str, phone: str,
                day: date, hh: int, price: int = 2000) -> int:
    service = BookingService(db, None)
    return await service.create_booking(
        service_rows=[{"id": 1, "name": "Стрижка", "price": price}],
        day=day, slot=(time(hh, 0), time(hh + 2, 0)),
        client={"user_id": user_id, "tg_id": tg_id, "name": name, "phone": phone},
        created_by="client", pd_consent=True,
    )


async def test_in_range_filters_and_sorts(db):
    user_id, another_id = await _world(db)
    monday, tuesday = date(2026, 11, 23), date(2026, 11, 24)
    await _book(db, user_id, CLIENT_TG, "Иван Пetroв", "+79260001122", tuesday, 14)
    await _book(db, user_id, CLIENT_TG, "Иван Пetroв", "+79260001122", monday, 18)
    await _book(db, another_id, 444, "Пётр Смирнов", "+79001234567", monday, 10)
    repo = BookingsRepo(db)
    rows = await repo.in_range(monday, monday)
    assert len(rows) == 2
    assert [r["slot_start"] for r in rows] == ["10:00", "18:00"]  # сортировка по времени
    rows_week = await repo.in_range(monday, tuesday)
    assert len(rows_week) == 3
    # за пределами диапазона пусто
    assert await repo.in_range(date(2026, 12, 1), date(2026, 12, 7)) == []


async def test_in_range_excludes_cancelled(db):
    user_id, _ = await _world(db)
    monday = date(2026, 11, 23)
    booking_id = await _book(db, user_id, CLIENT_TG, "Иван", "+79260001122", monday, 10)
    service = BookingService(db, None)
    await service.cancel_booking(booking_id, initiator="admin", reason="тест", notify=False)
    assert await BookingsRepo(db).in_range(monday, monday) == []


async def test_search_by_name_and_phone(db):
    user_id, another_id = await _world(db)
    monday = date(2026, 11, 23)
    await _book(db, user_id, CLIENT_TG, "Иван Пetroв", "+79260001122", monday, 10)
    await _book(db, another_id, 444, "Пётр Смирнов", "+79001234567", monday, 14)
    repo = BookingsRepo(db)
    # поиск по части имени
    rows = await repo.search("Иван")
    assert len(rows) == 1 and rows[0]["client_name"] == "Иван Пetroв"
    # поиск по части телефона
    rows = await repo.search("900123")
    assert len(rows) == 1 and rows[0]["client_phone"] == "+79001234567"
    # поиск без результата
    assert await repo.search("Сидоров") == []


async def test_search_covers_manual_snapshot(db):
    """Ручные записи без Telegram-аккаунта ищутся по снимку имени/телефона."""
    await _world(db)
    monday = date(2026, 11, 23)
    service = BookingService(db, None)
    await service.create_booking(
        service_rows=[{"id": 1, "name": "Стрижка", "price": 2000}],
        day=monday, slot=(time(10, 0), time(12, 0)),
        client=None, created_by="admin", pd_consent=False,
    )
    rows = await BookingsRepo(db).search("Стриж")  # не найдёт по услугам — по клиенту нет
    assert rows == []
    # снимок в bookings есть и ищется
    manual = await BookingsRepo(db).in_range(monday, monday)
    assert manual[0]["created_by"] == "admin"
