"""Репозиторий уведомлений: план напоминаний + статусы доставки.

Идемпотентность (п. 9.1 ТЗ): UNIQUE (booking_id, type) + INSERT OR IGNORE.
Мутирующие методы выполняются в транзакции сервиса/диспетчера.
"""

from datetime import datetime, timedelta
from typing import Optional

from bot.db.database import Database
from bot.utils.dates import now_msk, utcnow_iso

# Сроки напоминаний (п. 7.1 ТЗ): админу — за 1 час, клиенту — за 2 и за 24.
REMINDER_OFFSETS: dict[str, timedelta] = {
    "client_24h": timedelta(hours=24),
    "client_2h": timedelta(hours=2),
    "admin_1h": timedelta(hours=1),
}


class NotificationsRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def plan_for_booking(
        self,
        booking_id: int,
        visit_start: datetime,
        client_tg_id: Optional[int],
        now: Optional[datetime] = None,
    ) -> list[str]:
        """Создаёт планы напоминаний для новой записи (идемпотентно).

        Вызывается в транзакции создания записи. Возвращает типы планов.
        Для записей без Telegram-аккаунта клиента клиентские планы создаются
        сразу со статусом skipped (факт фиксируется, дублей при сверке нет).
        """
        now = now or now_msk()
        planned_types: list[str] = []
        for type_, offset in REMINDER_OFFSETS.items():
            planned_at = (visit_start - offset).isoformat()
            is_client = type_.startswith("client_")
            if is_client and client_tg_id is None:
                recipient, status = None, "skipped"
            elif visit_start - offset <= now:
                recipient = client_tg_id if is_client else 0
                status = "skipped"  # момент напоминания уже прошёл
            else:
                recipient = client_tg_id if is_client else 0
                status = "pending"
            await self.db.execute(
                "INSERT OR IGNORE INTO notifications "
                "(booking_id, type, recipient_tg_id, planned_at, status, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (booking_id, type_, recipient, planned_at, status, utcnow_iso()),
            )
            planned_types.append(type_)
        return planned_types

    async def skip_pending(self, booking_id: int) -> int:
        """Отмена записи — незавершённые планы гасим."""
        cursor = await self.db.execute(
            "UPDATE notifications SET status = 'skipped' "
            "WHERE booking_id = ? AND status IN ('pending', 'sending')",
            (booking_id,),
        )
        return cursor.rowcount

    async def due(self, now: datetime) -> list:
        return await self.db.fetchall(
            "SELECT n.id, n.booking_id, n.type, n.recipient_tg_id, n.planned_at, "
            "n.status FROM notifications n "
            "WHERE n.status = 'pending' AND n.planned_at <= ? "
            "ORDER BY n.planned_at LIMIT 50",
            (now.isoformat(),),
        )

    async def try_claim(self, notification_id: int) -> bool:
        """Атомарный перевод pending → sending (защита от повторной выдачи)."""
        cursor = await self.db.execute(
            "UPDATE notifications SET status = 'sending' WHERE id = ? AND status = 'pending'",
            (notification_id,),
        )
        return cursor.rowcount > 0

    async def mark_sent(self, notification_id: int) -> None:
        await self.db.execute(
            "UPDATE notifications SET status = 'sent', sent_at = ?, error = NULL WHERE id = ?",
            (utcnow_iso(), notification_id),
        )

    async def mark_failed(self, notification_id: int, error: str) -> None:
        await self.db.execute(
            "UPDATE notifications SET status = 'failed', error = ? WHERE id = ?",
            (error, notification_id),
        )

    async def mark_sending_as_sent(self) -> int:
        """Восстановление после рестарта: застрявшие sending считаются
        отправленными (семантика «не более одного раза» — без дублей, п. 9.1 ТЗ)."""
        cursor = await self.db.execute(
            "UPDATE notifications SET status = 'sent', error = 'unconfirmed (restart)' "
            "WHERE status = 'sending'"
        )
        return cursor.rowcount

    async def get(self, notification_id: int):
        return await self.db.fetchone(
            "SELECT * FROM notifications WHERE id = ?", (notification_id,)
        )

    async def booking_plans(self, booking_id: int) -> list:
        return await self.db.fetchall(
            "SELECT type, status, planned_at, recipient_tg_id, error "
            "FROM notifications WHERE booking_id = ?",
            (booking_id,),
        )

    async def mark_skipped(self, notification_id: int, note: str) -> None:
        """План гасится без отправки (например, запись отменена)."""
        await self.db.execute(
            "UPDATE notifications SET status = 'skipped', error = ? WHERE id = ?",
            (note, notification_id),
        )

    async def mark_client_plans_skipped_for_user(self, tg_id: int) -> None:
        """Автоотмена при блокировке бота: планы клиенту гасим."""
        await self.db.execute(
            "UPDATE notifications SET status = 'skipped' "
            "WHERE recipient_tg_id = ? AND status = 'pending' AND type LIKE 'client_%'",
            (tg_id,),
        )
