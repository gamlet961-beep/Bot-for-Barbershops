"""Фаза 8: анонимизация ПД (п. 9.2 ТЗ; A.8.1–A.8.3) и окно «Моих записей»."""

from datetime import date, time, timedelta

from bot.db.repositories.bookings import BookingsRepo
from bot.db.repositories.schedule import ScheduleRepo
from bot.db.repositories.users import UsersRepo
from bot.services.booking import BookingService
from bot.services.privacy import PrivacyService
from bot.services.scheduling import DayRule
from bot.utils.dates import date_months_ago, today_msk

CLIENT_TG = 333
OTHER_TG = 444
TODAY = date(2026, 11, 21)


async def _world(db):
    users = UsersRepo(db)
    uid = await users.upsert_by_telegram(CLIENT_TG, "client")
    await users.set_contact(uid, "Иван", "+79260001122")
    other_uid = await users.upsert_by_telegram(OTHER_TG, "other")
    await users.set_contact(other_uid, "Пётр", "+79005556677")
    async with db.transaction():
        for weekday in range(7):  # рабочая неделя целиком — даты тестов на любых днях
            await ScheduleRepo(db).upsert_template(weekday, DayRule.work(time(10, 0), time(20, 0)))
        await db.execute(
            "INSERT INTO services (id, category_id, name, price, is_active, sort_order) "
            "VALUES (1, 1, 'Стрижка', 2000, 1, 1)"
        )
    return uid, other_uid


async def _book(db, user_id: int, day: date, hh: int = 18) -> int:
    service = BookingService(db, None)
    return await service.create_booking(
        service_rows=[{"id": 1, "name": "Стрижка", "price": 2000}],
        day=day, slot=(time(hh, 0), time(hh + 2, 0)),
        client={"user_id": user_id, "tg_id": CLIENT_TG, "name": "Иван", "phone": "+79260001122"},
        created_by="client", pd_consent=True,
    )


async def test_anonymize_old_booking_keeps_statistics(db):
    uid, _ = await _world(db)
    old_day = date_months_ago(TODAY, 3)  # визит 3 месяца назад
    booking_id = await _book(db, uid, old_day)
    stats = await PrivacyService(db).anonymize_expired(today=TODAY)
    assert stats["bookings"] == 1
    row = await BookingsRepo(db).get(booking_id)
    assert row["client_name"] == "[анонимизировано]"
    assert row["client_phone"] is None
    # A.8.2: дата, время, услуги и сумма сохраняются для статистики
    assert row["slot_date"] == old_day.isoformat()
    assert row["slot_start"] == "18:00"
    services = await db.fetchall(
        "SELECT name_at_booking, price_at_booking FROM booking_services WHERE booking_id = ?",
        (booking_id,),
    )
    assert services[0]["name_at_booking"] == "Стрижка"
    assert row["total_price"] == 2000


async def test_anonymize_boundary_and_recent(db):
    uid, _ = await _world(db)
    cutoff = date_months_ago(TODAY, 2)
    boundary_id = await _book(db, uid, cutoff)      # ровно 2 месяца — ещё в окне
    recent_id = await _book(db, uid, TODAY + timedelta(days=5))
    stats = await PrivacyService(db).anonymize_expired(today=TODAY)
    assert stats["bookings"] == 0
    for booking_id in (boundary_id, recent_id):
        row = await BookingsRepo(db).get(booking_id)
        assert row["client_name"] == "Иван"
        assert row["client_phone"] == "+79260001122"


async def test_anonymize_idempotent(db):
    uid, _ = await _world(db)
    old_day = date_months_ago(TODAY, 3)
    await _book(db, uid, old_day)
    first = await PrivacyService(db).anonymize_expired(today=TODAY)
    second = await PrivacyService(db).anonymize_expired(today=TODAY)
    assert first["bookings"] == 1 and second["bookings"] == 0


async def test_user_profile_cleaned_without_active_window_bookings(db):
    uid, other_uid = await _world(db)
    # у OTHER_TG записей нет вовсе; у CLIENT_TG — свежая запись
    await _book(db, uid, TODAY + timedelta(days=5))
    stats = await PrivacyService(db).anonymize_expired(today=TODAY)
    other_user = await UsersRepo(db).get_by_telegram_id(OTHER_TG)
    assert other_user["name"] is None and other_user["phone"] is None
    client_user = await UsersRepo(db).get_by_telegram_id(CLIENT_TG)
    assert client_user["name"] == "Иван"  # есть запись в окне — ПД на месте


async def test_my_bookings_window_filters(db):
    """A.6.1: только записи за последние 2 месяца; отменённые скрыты (A.6.3)."""
    uid, _ = await _world(db)
    cutoff = date_months_ago(TODAY, 2)
    await _book(db, uid, TODAY - timedelta(days=30))     # в окне (прошлая)
    await _book(db, uid, TODAY + timedelta(days=5))      # будущая
    old_id = await _book(db, uid, cutoff - timedelta(days=1))  # вне окна
    cancelled_id = await _book(db, uid, TODAY + timedelta(days=6))
    service = BookingService(db, None)
    await service.cancel_booking(cancelled_id, initiator="client", reason="тест", notify=False)
    rows = await BookingsRepo(db).active_for_user(uid, cutoff)
    assert {r["id"] for r in rows} == {
        r["id"] for r in rows if r["id"] not in (old_id, cancelled_id)
    }
    assert old_id not in {r["id"] for r in rows}
    assert cancelled_id not in {r["id"] for r in rows}
    assert len(rows) == 2
