"""Фаза 4: услуги и категории (п. 4.3 ТЗ; A.3.1–A.3.6, UC-5 акта)."""

from datetime import date, time

from bot.db.repositories.bookings import BookingsRepo
from bot.db.repositories.schedule import ScheduleRepo
from bot.db.repositories.services import ServicesRepo
from bot.services.booking import BookingService
from bot.services.scheduling import DayRule


async def _world(db):
    async with db.transaction():
        await ScheduleRepo(db).upsert_template(0, DayRule.work(time(10, 0), time(20, 0)))
    repo = ServicesRepo(db)
    await db.execute(
        "INSERT INTO users (telegram_id, username, created_at) VALUES (333, NULL, '2026-01-01')"
    )
    return repo


async def test_categories_seeded(db):
    repo = await _world(db)
    categories = await repo.categories()
    assert [c["name"] for c in categories] == ["Стрижка", "Борода и усы"]


async def test_create_and_get_service(db):
    repo = await _world(db)
    service_id = await repo.create(
        category_id=1, name="Мужская стрижка", description="Классика", price=2000
    )
    service = await repo.get(service_id)
    assert service["name"] == "Мужская стрижка"
    assert service["price"] == 2000
    assert service["category_name"] == "Стрижка"
    assert service["is_active"] == 1


async def test_update_fields(db):
    repo = await _world(db)
    service_id = await repo.create(
        category_id=1, name="Стрижка", description=None, price=1500
    )
    assert await repo.update(service_id, name="Стрижка машинкой", price=1200,
                             description="Быстро", category_id=2)
    service = await repo.get(service_id)
    assert service["name"] == "Стрижка машинкой"
    assert service["price"] == 1200
    assert service["description"] == "Быстро"
    assert service["category_id"] == 2 and service["category_name"] == "Борода и усы"


async def test_hide_excludes_from_active_but_not_from_all(db):
    repo = await _world(db)
    first = await repo.create(category_id=1, name="Стрижка", description=None, price=2000)
    second = await repo.create(category_id=2, name="Борода", description=None, price=1000)
    await repo.set_active(first, False)
    active = await repo.list(only_active=True)
    assert [s["id"] for s in active] == [second]
    everything = await repo.list(only_active=False)
    assert {s["id"] for s in everything} == {first, second}


async def test_hidden_service_keeps_bookings_active(db):
    """A.3.5: скрытие услуги не трогает существующие и будущие записи."""
    repo = await _world(db)
    service_id = await repo.create(category_id=1, name="Стрижка", description=None, price=2000)
    service = {"id": service_id, "name": "Стрижка", "price": 2000}
    booking_service = BookingService(db, None)
    day = date(2026, 12, 7)
    booking_id = await booking_service.create_booking(
        service_rows=[service], day=day, slot=(time(10, 0), time(12, 0)),
        client={"user_id": 1, "tg_id": 333, "name": "Иван", "phone": "+79000000000"},
        created_by="client", pd_consent=True,
    )
    await repo.set_active(service_id, False)
    row = await BookingsRepo(db).get(booking_id)
    assert row["status"] == "active"
    # админ по-прежнему видит запись
    bookings = await BookingsRepo(db).active_for_day(day)
    assert len(bookings) == 1 and bookings[0]["id"] == booking_id
    # и услуга видна админу в полном списке
    assert any(s["id"] == service_id for s in await repo.list(only_active=False))


async def test_grouping_by_category(db):
    repo = await _world(db)
    await repo.create(category_id=1, name="Стрижка", description=None, price=2000)
    await repo.create(category_id=1, name="Стрижка + борода", description=None, price=3000)
    await repo.create(category_id=2, name="Оформление бороды", description=None, price=1000)
    grouped = await repo.active_by_category()
    assert [s["name"] for s in grouped[1]] == ["Стрижка", "Стрижка + борода"]
    assert [s["name"] for s in grouped[2]] == ["Оформление бороды"]
