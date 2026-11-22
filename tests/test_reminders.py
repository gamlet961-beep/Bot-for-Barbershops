"""Фаза 7: планировщик напоминаний — идемпотентность, восстановление,
автоотмена при блокировке бота (ТЗ, п. 7, 9.1; A.7.2–A.7.9, B.2, B.3, UC-9)."""

from datetime import date, time, timedelta

from bot.db.repositories.admins import AdminsRepo
from bot.db.repositories.bookings import BookingsRepo
from bot.db.repositories.notifications import NotificationsRepo
from bot.db.repositories.schedule import ScheduleRepo
from bot.db.repositories.users import UsersRepo
from bot.services.booking import BookingService
from bot.services.notifications import Notificator
from bot.services.reminders import ReminderService
from bot.services.scheduling import DayRule
from bot.utils.dates import MSK, now_msk, today_msk

PRIMARY_TG = 111
SECOND_TG = 222
CLIENT_TG = 333


class ScriptedNotificator(Notificator):
    """Фиксирует отправки; для указанных chat_id возвращает 'blocked'."""

    def __init__(self, db, blocked_for=frozenset()) -> None:
        super().__init__(None, db)
        self.sent: list[tuple[int, str]] = []
        self.blocked_for = set(blocked_for)

    async def send_message(self, chat_id, text):  # type: ignore[override]
        if chat_id in self.blocked_for:
            return "blocked"
        self.sent.append((chat_id, text))
        return "sent"


async def _world(db) -> None:
    admins = AdminsRepo(db)
    await admins.ensure_primary(PRIMARY_TG)
    await admins.add_admin(SECOND_TG, added_by=PRIMARY_TG)
    users = UsersRepo(db)
    user_id = await users.upsert_by_telegram(CLIENT_TG, "client")
    await users.set_contact(user_id, "Иван", "+79260001122")
    async with db.transaction():
        await ScheduleRepo(db).upsert_template((today_msk() + timedelta(days=3)).weekday(),
                                               DayRule.work(time(10, 0), time(20, 0)))
    async with db.transaction():
        await db.execute(
            "INSERT INTO services (id, category_id, name, price, is_active, sort_order) "
            "VALUES (1, 1, 'Стрижка', 2000, 1, 1)"
        )


async def _book(db, day: date, hh: int = 18) -> int:
    service = BookingService(db, None)
    return await service.create_booking(
        service_rows=[{"id": 1, "name": "Стрижка", "price": 2000}],
        day=day, slot=(time(hh, 0), time(hh + 2, 0)),
        client={"user_id": 1, "tg_id": CLIENT_TG, "name": "Иван", "phone": "+79260001122"},
        created_by="client", pd_consent=True,
    )


def _visit(day: date, hh: int = 18):
    from bot.utils.dates import combine_msk

    return combine_msk(day, time(hh, 0))


async def test_dispatch_admin_reminder_1h(db):
    """A.4.7: напоминание за 1 час по ручной записи (клиентских планов нет)."""
    await _world(db)
    day = today_msk() + timedelta(days=3)
    manual = BookingService(db, None)
    booking_id = await manual.create_booking(
        service_rows=[{"id": 1, "name": "Стрижка", "price": 2000}],
        day=day, slot=(time(18, 0), time(20, 0)),
        client={"user_id": None, "tg_id": None, "name": "Иван", "phone": "+79260001122"},
        created_by="admin", pd_consent=False,
    )
    notificator = ScriptedNotificator(db)
    reminders = ReminderService(db, notificator, BookingService(db, notificator))
    visit = _visit(day)
    stats = await reminders.dispatch_due(now=visit - timedelta(hours=1, minutes=-1))
    # A.7.2: напоминание за 1 час — обоим админам, и только оно
    admin_sends = [(c, t) for c, t in notificator.sent if c in (PRIMARY_TG, SECOND_TG)]
    assert len(admin_sends) == 2
    assert all(t == "⏰ Через час клиент Иван записан на Стрижка в 18:00." for _, t in admin_sends)
    assert stats["sent"] == 1
    plans = {p["type"]: p for p in await NotificationsRepo(db).booking_plans(booking_id)}
    assert plans["admin_1h"]["status"] == "sent"
    # A.7.9 идемпотентность: повторный прогон ничего не шлёт
    stats2 = await reminders.dispatch_due(now=visit - timedelta(hours=1, minutes=-1))
    assert stats2["sent"] == 0
    assert len(notificator.sent) == 2


async def test_dispatch_client_reminders_2h_and_24h(db):
    await _world(db)
    day = today_msk() + timedelta(days=3)
    booking_id = await _book(db, day)
    notificator = ScriptedNotificator(db)
    reminders = ReminderService(db, notificator, BookingService(db, notificator))
    visit = _visit(day)

    stats24 = await reminders.dispatch_due(now=visit - timedelta(hours=24, minutes=-1))
    client_24 = [t for c, t in notificator.sent if c == CLIENT_TG]
    assert stats24["sent"] == 1
    assert client_24 and "через 24 ч" in client_24[0]

    stats2 = await reminders.dispatch_due(now=visit - timedelta(hours=2, minutes=-1))
    client_2 = [t for c, t in notificator.sent if c == CLIENT_TG]
    assert stats2["sent"] == 1
    assert len(client_2) == 2 and "через 2 ч" in client_2[1]
    plans = {p["type"]: p for p in await NotificationsRepo(db).booking_plans(booking_id)}
    assert plans["client_24h"]["status"] == "sent"
    assert plans["client_2h"]["status"] == "sent"


async def test_dispatch_skips_cancelled_booking(db):
    await _world(db)
    day = today_msk() + timedelta(days=3)
    booking_id = await _book(db, day)
    notificator = ScriptedNotificator(db)
    booking_service = BookingService(db, notificator)
    reminders = ReminderService(db, notificator, booking_service)
    await booking_service.cancel_booking(booking_id, initiator="client", reason="тест", notify=False)
    notificator.sent.clear()
    visit = _visit(day)
    stats = await reminders.dispatch_due(now=visit + timedelta(minutes=1))
    # планы уже погашены отменой (skip_pending в cancel_booking) — отправок нет
    assert stats["sent"] == 0
    assert not notificator.sent
    plans = await NotificationsRepo(db).booking_plans(booking_id)
    assert all(p["status"] == "skipped" for p in plans)


async def test_blocked_bot_autocancels_booking(db):
    """A.7.8 / UC-9: клиент заблокировал бота → автоотмена + уведомление админам."""
    await _world(db)
    day = today_msk() + timedelta(days=3)
    booking_id = await _book(db, day)
    # клиент блокирует бота: все отправки клиенту падают с blocked
    notificator = ScriptedNotificator(db, blocked_for={CLIENT_TG})
    booking_service = BookingService(db, notificator)
    reminders = ReminderService(db, notificator, booking_service)
    visit = _visit(day)
    stats = await reminders.dispatch_due(now=visit - timedelta(hours=2, minutes=-1))
    assert stats["blocked"] == 1
    row = await BookingsRepo(db).get(booking_id)
    assert row["status"] == "cancelled"
    assert row["cancel_initiator"] == "auto_blocked"
    slot = await db.fetchone("SELECT is_active FROM booking_slots WHERE booking_id = ?", (booking_id,))
    assert slot["is_active"] == 0
    # админам ушло «Запись отменена, клиент заблокировал бота» с деталями
    admin_texts = [t for c, t in notificator.sent if c in (PRIMARY_TG, SECOND_TG)]
    assert len(admin_texts) == 2
    assert all(t.startswith("🚫 Запись отменена, клиент заблокировал бота") for t in admin_texts)
    # оставшиеся клиентские планы погашены
    plans = await NotificationsRepo(db).booking_plans(booking_id)
    assert all(p["status"] in ("skipped", "failed") for p in plans)


async def test_reconcile_restores_missing_plans(db):
    """B.2: после рестарта планы восстанавливаются (досоздаются), без дублей."""
    await _world(db)
    day = today_msk() + timedelta(days=3)
    booking_id = await _book(db, day)
    # симулируем частичный сбой: планы потеряны
    await db.execute("DELETE FROM notifications WHERE booking_id = ?", (booking_id,))
    notificator = ScriptedNotificator(db)
    reminders = ReminderService(db, notificator, BookingService(db, notificator))
    stats = await reminders.reconcile()
    assert stats["planned"] >= 1
    plans = {p["type"]: p for p in await NotificationsRepo(db).booking_plans(booking_id)}
    assert set(plans) == {"client_24h", "client_2h", "admin_1h"}
    assert all(p["status"] == "pending" for p in plans.values())
    # повторный reconcile не дублирует (UNIQUE booking_id+type)
    stats2 = await reminders.reconcile()
    count = await db.fetchval(
        "SELECT COUNT(*) FROM notifications WHERE booking_id = ?", (booking_id,)
    )
    assert count == 3


async def test_reconcile_marks_stale_sending_as_sent(db):
    """B.2: застрявшие в sending помечаются отправленными — без дублей."""
    await _world(db)
    day = today_msk() + timedelta(days=3)
    booking_id = await _book(db, day)
    await db.execute(
        "UPDATE notifications SET status = 'sending' "
        "WHERE booking_id = ? AND type = 'client_2h'",
        (booking_id,),
    )
    notificator = ScriptedNotificator(db)
    reminders = ReminderService(db, notificator, BookingService(db, notificator))
    stats = await reminders.reconcile()
    assert stats["unconfirmed"] == 1
    plans = {p["type"]: p for p in await NotificationsRepo(db).booking_plans(booking_id)}
    assert plans["client_2h"]["status"] == "sent"
    assert plans["client_2h"]["error"] == "unconfirmed (restart)"


async def test_dispatch_does_not_resend_after_restart(db):
    """Сценарий B.2: рестарт процесса между отправкой и фиксацией — дубли нет."""
    await _world(db)
    day = today_msk() + timedelta(days=3)
    booking_id = await _book(db, day)
    await db.execute(
        "UPDATE notifications SET status = 'sending' "
        "WHERE booking_id = ? AND type = 'client_2h'",
        (booking_id,),
    )
    notificator = ScriptedNotificator(db)
    reminders = ReminderService(db, notificator, BookingService(db, notificator))
    await reminders.reconcile()
    visit = _visit(day)
    stats = await reminders.dispatch_due(now=visit - timedelta(hours=2, minutes=-1))
    # client_2h уже «sent» (unconfirmed) — повторно не отправляется
    client_2_sends = [t for c, t in notificator.sent
                      if c == CLIENT_TG and "через 2 ч" in t]
    assert len(client_2_sends) == 0
