"""Планировщик напоминаний: диспетчер и восстановление (ТЗ, п. 7, 9.1).

Диспетчер запускается раз в минуту (APScheduler):
- выбирает наступившие планы (pending, planned_at <= now);
- атомарно переводит их в sending (защита от повторной выдачи);
- отправляет: админ_1ч — всем админам, клиентские — получателю;
- результат — sent / failed / skipped (запись отменена).

Идемпотентность (A.7.9, B.2): уникальность (booking_id, type) + claim по
условию + перевод статуса только вперёд — повторный прогон ничего не дублирует.

Блокировка бота (п. 7.3 ТЗ; A.7.8, UC-9): ошибка доставки клиентского
напоминания → автоотмена записи, освобождение слота, уведомление админам.

Восстановление при старте (reconcile): застрявшие sending помечаются
отправленными без подтверждения (семантика «не более одного раза» — без
дублей); недостающие планы у активных будущих записей досоздаются;
планы отменённых записей гасятся.
"""

import logging
from datetime import date, timedelta
from typing import Optional

from bot.db.database import Database
from bot.db.repositories.admins import AdminsRepo
from bot.db.repositories.bookings import BookingsRepo
from bot.db.repositories.notifications import NotificationsRepo
from bot.services.booking import BookingService
from bot.services.notifications import Notificator
from bot.utils import formatting as fmt
from bot.utils.dates import combine_msk, now_msk, parse_hhmm

logger = logging.getLogger(__name__)


class ReminderService:
    def __init__(
        self,
        db: Database,
        notificator: Notificator,
        booking_service: BookingService,
    ) -> None:
        self.db = db
        self.notificator = notificator
        self.booking_service = booking_service

    async def dispatch_due(self, now: Optional = None) -> dict:
        """Разовая диспетчеризация наступивших напоминаний. Идемпотентна."""
        now = now or now_msk()
        stats = {"sent": 0, "failed": 0, "blocked": 0, "skipped": 0}
        notifications = NotificationsRepo(self.db)
        bookings = BookingsRepo(self.db)

        for row in await notifications.due(now):
            # запись могла быть отменена после выборки плана
            booking = await bookings.get(row["booking_id"])
            if booking is None or booking["status"] != "active":
                await notifications.mark_skipped(row["id"], "booking cancelled")
                stats["skipped"] += 1
                continue
            if not await notifications.try_claim(row["id"]):
                continue  # уже забрал другой проход (идемпотентность)

            view = await bookings.get_view(row["booking_id"])
            if view is None:
                await notifications.mark_failed(row["id"], "view not found")
                continue

            result = await self._send(row, view)
            if result == "blocked":
                # П. 7.3 ТЗ: клиент заблокировал бота → автоотмена
                logger.warning(
                    "Клиент %s заблокировал бота: автоотмена записи #%s",
                    row["recipient_tg_id"], row["booking_id"],
                )
                await notifications.mark_failed(row["id"], "blocked")
                await self.booking_service.cancel_booking(
                    row["booking_id"],
                    initiator="auto_blocked",
                    reason="клиент заблокировал бота",
                )
                stats["blocked"] += 1
            elif result == "sent":
                await notifications.mark_sent(row["id"])
                stats["sent"] += 1
                logger.info(
                    "Напоминание %s по записи #%s отправлено",
                    row["type"], row["booking_id"],
                )
            else:
                await notifications.mark_failed(row["id"], "delivery failed")
                stats["failed"] += 1
        return stats

    async def _send(self, row, view: dict) -> str:
        if row["type"] == "admin_1h":
            # всем администраторам (получатель «Админ(ы)», п. 7.1 ТЗ)
            for tg_id in await AdminsRepo(self.db).admin_tg_ids():
                await self.notificator.send_message(tg_id, fmt.admin_reminder_1h(view))
            return "sent"
        hours = 24 if row["type"] == "client_24h" else 2
        return await self.notificator.send_message(
            row["recipient_tg_id"], fmt.client_reminder(view, hours)
        )

    async def reconcile(self) -> dict:
        """Восстановление при запуске процесса (п. 9.1 ТЗ; B.2)."""
        notifications = NotificationsRepo(self.db)
        stats = {
            "unconfirmed": await notifications.mark_sending_as_sent(),
            "planned": 0,
            "skipped": 0,
        }
        now = now_msk()
        rows = await self.db.fetchall(
            "SELECT b.id, s.date, s.slot_start, u.telegram_id AS client_tg_id "
            "FROM bookings b "
            "JOIN booking_slots s ON s.booking_id = b.id "
            "LEFT JOIN users u ON u.id = b.client_id "
            "WHERE b.status = 'active'"
        )
        for row in rows:
            day = date.fromisoformat(row["date"])
            slot_start = parse_hhmm(row["slot_start"])
            visit = combine_msk(day, slot_start)
            if visit <= now:
                continue  # прошедший визит — напоминания неактуальны
            await notifications.plan_for_booking(
                row["id"], visit, row["client_tg_id"], now=now
            )
            stats["planned"] += 1
        cursor = await self.db.execute(
            "UPDATE notifications SET status = 'skipped' "
            "WHERE status = 'pending' AND booking_id IN "
            "(SELECT id FROM bookings WHERE status = 'cancelled')"
        )
        stats["skipped"] = cursor.rowcount
        logger.info("Восстановление напоминаний: %s", stats)
        return stats
