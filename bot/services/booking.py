"""Сервис записей: единые точки создания, отмены и отмены рабочего дня.

Гарантии (п. 6.2 ТЗ):
- создание записи — одна транзакция BEGIN IMMEDIATE: проверка дня, проверка
  свободности слота, вставка записи/слота/услуг/планов напоминаний;
- частичный уникальный индекс ux_active_slot — вторая линия защиты от гонок;
- отмена любой записи — одна функция независимо от инициатора.

Мгновенные уведомления отправляются ПОСЛЕ коммита транзакции; при создании
записи админом вручную не отправляются вовсе (п. 4.5 ТЗ).
"""

import logging
from datetime import date, time
from typing import Optional

from bot.db.database import Database
from bot.db.repositories.bookings import BookingsRepo
from bot.db.repositories.notifications import NotificationsRepo
from bot.db.repositories.schedule import ScheduleRepo
from bot.services.notifications import Notificator
from bot.services.scheduling import DayRule, slot_key
from bot.utils import formatting as fmt
from bot.utils.dates import combine_msk, fmt_date, now_msk, utcnow_iso

logger = logging.getLogger(__name__)


class SlotTakenError(Exception):
    """Слот уже занят активной записью или заблокирован (UC-7, A.5.10)."""


class DayOffError(Exception):
    """Выбранный день не является рабочим."""


class BookingService:
    def __init__(self, db: Database, notificator: Optional[Notificator] = None) -> None:
        self.db = db
        self.notificator = notificator

    async def create_booking(
        self,
        *,
        service_rows: list[dict],
        day: date,
        slot: tuple[time, time],
        client: Optional[dict],
        created_by: str,
        pd_consent: bool,
    ) -> int:
        """Создаёт запись.

        service_rows: [{'id', 'name', 'price'}, ...] — снимки цен фиксируются.
        client: {'user_id', 'tg_id', 'name', 'phone'} | None (ручная запись
        без совпадения по телефону).
        created_by: 'client' | 'admin' (п. 4.5 ТЗ — различия в уведомлениях).
        """
        slot_start, slot_end = slot
        total = sum(int(s["price"]) for s in service_rows)
        repo = BookingsRepo(self.db)
        schedule = ScheduleRepo(self.db)

        async with self.db.transaction():
            rule = await schedule.resolve_working_day(day)
            if not rule.is_working or rule.start is None or rule.end is None:
                raise DayOffError(day)
            taken = await repo.active_slot_starts(day)
            blocked = await schedule.blocked_slot_starts(day)
            key = slot_key(slot_start)
            if key in taken or key in blocked:
                raise SlotTakenError(day, slot_start)
            booking_id = await repo.insert(
                client_id=client["user_id"] if client else None,
                client_name=client["name"] if client else None,
                client_phone=client["phone"] if client else None,
                created_by=created_by,
                total_price=total,
                pd_consent_at=utcnow_iso() if pd_consent else None,
                day=day,
                slot_start=slot_start,
                slot_end=slot_end,
            )
            await repo.add_services(booking_id, service_rows)
            await NotificationsRepo(self.db).plan_for_booking(
                booking_id,
                combine_msk(day, slot_start),
                client["tg_id"] if client else None,
            )
        logger.info(
            "Создана запись #%s: %s %s %s-%s, инициатор=%s",
            booking_id, day, key, slot_start, slot_end, created_by,
        )

        # Мгновенные уведомления — только для записи, созданной клиентом (п. 4.5 ТЗ).
        if self.notificator and created_by == "client" and client:
            view = await repo.get_view(booking_id)
            if view:
                await self.notificator.send_message(
                    client["tg_id"], fmt.booking_confirmation(view)
                )
                await self.notificator.notify_admins(fmt.admin_new_booking(view))
        return booking_id

    async def cancel_booking(
        self,
        booking_id: int,
        *,
        initiator: str,
        reason: str,
        notify: bool = True,
    ) -> bool:
        """Отмена записи любым инициатором: client / admin / auto_day / auto_blocked.

        Гасит планы напоминаний, освобождает слот, отправляет уведомления
        по матрице ТЗ (п. 4.6, 7.1). Повторная отмена безопасна (False).
        """
        repo = BookingsRepo(self.db)
        async with self.db.transaction():
            cancelled = await repo.cancel(booking_id, reason, initiator, utcnow_iso())
            if cancelled:
                await NotificationsRepo(self.db).skip_pending(booking_id)
        if not cancelled:
            return False
        logger.info(
            "Отменена запись #%s (инициатор=%s, причина=%s)", booking_id, initiator, reason
        )

        if notify and self.notificator:
            view = await repo.get_view(booking_id)
            if view:
                if initiator == "client":
                    await self.notificator.notify_admins(fmt.admin_client_cancelled(view))
                    if view["client_tg_id"]:
                        # П. 5.7 ТЗ: клиенту — подтверждение отмены
                        await self.notificator.send_message(
                            view["client_tg_id"], fmt.client_cancel_self(view)
                        )
                elif initiator == "admin":
                    if view["client_tg_id"]:
                        await self.notificator.send_message(
                            view["client_tg_id"], fmt.client_cancelled_by_admin(view)
                        )
                elif initiator == "auto_day":
                    if view["client_tg_id"]:
                        await self.notificator.send_message(
                            view["client_tg_id"], fmt.client_day_cancelled(view)
                        )
                elif initiator == "auto_blocked":
                    await self.notificator.notify_admins(fmt.admin_blocked_bot(view))
        return True

    async def count_active_bookings(self, day: date) -> int:
        return await BookingsRepo(self.db).count_active_for_day(day)

    async def cancel_working_day(self, day: date) -> list[dict]:
        """Удаление рабочего дня (п. 4.2 ТЗ): исключение «день отменён» +
        автоотмена активных записей + уведомления клиентам + отчёт админам.

        Возвращает представления отменённых записей (для отчёта и тестов).
        """
        repo = BookingsRepo(self.db)
        cancelled_ids: list[int] = []
        async with self.db.transaction():
            await ScheduleRepo(self.db).upsert_exception(
                day, DayRule.off(), note="рабочий день отменён"
            )
            rows = await repo.active_for_day(day)
            for row in rows:
                if await repo.cancel(
                    row["id"], "рабочий день отменён", "auto_day", utcnow_iso()
                ):
                    await NotificationsRepo(self.db).skip_pending(row["id"])
                    cancelled_ids.append(row["id"])
        logger.info("Рабочий день %s отменён; отменено записей: %d", day, len(cancelled_ids))

        views: list[dict] = []
        for booking_id in cancelled_ids:
            view = await repo.get_view(booking_id)
            if view:
                views.append(view)
                if self.notificator and view["client_tg_id"]:
                    await self.notificator.send_message(
                        view["client_tg_id"], fmt.client_day_cancelled(view)
                    )
        if self.notificator and views:
            await self.notificator.notify_admins(fmt.admin_day_report(fmt_date(day), views))
        return views
