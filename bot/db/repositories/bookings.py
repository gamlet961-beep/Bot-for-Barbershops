"""Репозиторий записей и слотов.

Мутирующие методы выполняются в транзакции сервиса (BookingService),
сами по себе не коммитят. INSERT-методы возвращают id для связывания.
"""

from datetime import date, time
from typing import Iterable, Optional

from bot.db.database import Database
from bot.utils.dates import utcnow_iso

BOOKING_SELECT = (
    "SELECT b.*, s.date AS slot_date, s.slot_start, s.slot_end, "
    "u.telegram_id AS client_tg_id "
    "FROM bookings b "
    "JOIN booking_slots s ON s.booking_id = b.id "
    "LEFT JOIN users u ON u.id = b.client_id "
)


class BookingsRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def insert(
        self,
        *,
        client_id: Optional[int],
        client_name: Optional[str],
        client_phone: Optional[str],
        created_by: str,
        total_price: int,
        pd_consent_at: Optional[str],
        day: date,
        slot_start: time,
        slot_end: time,
    ) -> int:
        cursor = await self.db.execute(
            "INSERT INTO bookings (client_id, client_name, client_phone, created_by, "
            "status, total_price, pd_consent_at, created_at) "
            "VALUES (?, ?, ?, ?, 'active', ?, ?, ?)",
            (client_id, client_name, client_phone, created_by, total_price,
             pd_consent_at, utcnow_iso()),
        )
        booking_id = cursor.lastrowid
        await self.db.execute(
            "INSERT INTO booking_slots (booking_id, date, slot_start, slot_end, is_active) "
            "VALUES (?, ?, ?, ?, 1)",
            (booking_id, day.isoformat(), slot_start.strftime("%H:%M"),
             slot_end.strftime("%H:%M")),
        )
        return booking_id  # type: ignore[return-value]

    async def add_services(self, booking_id: int, service_rows: Iterable[dict]) -> None:
        for service in service_rows:
            await self.db.execute(
                "INSERT INTO booking_services (booking_id, service_id, name_at_booking, "
                "price_at_booking) VALUES (?, ?, ?, ?)",
                (booking_id, service["id"], service["name"], service["price"]),
            )

    async def active_slot_starts(self, day: date) -> set[str]:
        rows = await self.db.fetchall(
            "SELECT s.slot_start FROM booking_slots s "
            "JOIN bookings b ON b.id = s.booking_id "
            "WHERE s.date = ? AND s.is_active = 1 AND b.status = 'active'",
            (day.isoformat(),),
        )
        return {row["slot_start"] for row in rows}

    async def get(self, booking_id: int):
        return await self.db.fetchone(
            BOOKING_SELECT + "WHERE b.id = ?", (booking_id,)
        )

    async def get_view(self, booking_id: int) -> Optional[dict]:
        """Полное представление записи для сообщений и интерфейсов."""
        row = await self.get(booking_id)
        if row is None:
            return None
        services = await self.db.fetchall(
            "SELECT name_at_booking, price_at_booking FROM booking_services "
            "WHERE booking_id = ? ORDER BY id",
            (booking_id,),
        )
        from bot.utils.formatting import build_view

        return build_view(row, services)

    async def active_for_day(self, day: date) -> list:
        return await self.db.fetchall(
            BOOKING_SELECT + "WHERE s.date = ? AND b.status = 'active' "
            "ORDER BY s.slot_start",
            (day.isoformat(),),
        )

    async def count_active_for_day(self, day: date) -> int:
        return await self.db.fetchval(
            "SELECT COUNT(*) FROM bookings b JOIN booking_slots s ON s.booking_id = b.id "
            "WHERE s.date = ? AND b.status = 'active'",
            (day.isoformat(),),
        ) or 0

    async def cancel(self, booking_id: int, reason: str, initiator: str, now_iso: str) -> bool:
        """Переводит запись в cancelled и освобождает слот (в транзакции сервиса)."""
        cursor = await self.db.execute(
            "UPDATE bookings SET status = 'cancelled', cancelled_at = ?, "
            "cancel_reason = ?, cancel_initiator = ? "
            "WHERE id = ? AND status = 'active'",
            (now_iso, reason, initiator, booking_id),
        )
        if not cursor.rowcount:
            return False
        await self.db.execute(
            "UPDATE booking_slots SET is_active = 0 WHERE booking_id = ?", (booking_id,)
        )
        return True

    async def active_for_user(self, user_id: int, date_from: date) -> list:
        return await self.db.fetchall(
            BOOKING_SELECT + "WHERE b.client_id = ? AND b.status = 'active' "
            "AND s.date >= ? ORDER BY s.date, s.slot_start",
            (user_id, date_from.isoformat()),
        )

    # ── раздел «Записи» админа (п. 4.4 ТЗ; A.4.1–A.4.3) ──

    async def in_range(self, from_day: date, to_day: date) -> list:
        """Активные записи в диапазоне дат, сортировка по дате и времени."""
        return await self.db.fetchall(
            BOOKING_SELECT + "WHERE s.date BETWEEN ? AND ? AND b.status = 'active' "
            "ORDER BY s.date, s.slot_start",
            (from_day.isoformat(), to_day.isoformat()),
        )

    async def search(self, query: str) -> list:
        """Поиск активных записей по имени и телефону (включая снимки ручных записей)."""
        like = f"%{query.strip()}%"
        return await self.db.fetchall(
            BOOKING_SELECT + "WHERE b.status = 'active' "
            "AND (b.client_name LIKE ? OR b.client_phone LIKE ?) "
            "ORDER BY s.date, s.slot_start LIMIT 30",
            (like, like),
        )
