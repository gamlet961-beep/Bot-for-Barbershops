"""Анонимизация персональных данных (ТЗ, п. 9.2; A.8.1–A.8.3).

Ежедневная задача (03:00 MSK): у записей, чей визит завершился более
2 месяцев назад, имя и телефон заменяются на обезличенные значения.
Дата, время, услуги и сумма сохраняются для статистики. Профиль пользователя
обезличивается, если у него не осталось записей в 2-месячном окне.
Необратимо; факт фиксируется флагом is_anonymized (идемпотентность).
"""

import logging
from datetime import date

from bot.db.database import Database
from bot.utils.dates import date_months_ago, today_msk

logger = logging.getLogger(__name__)

ANONYMIZED_NAME = "[анонимизировано]"


class PrivacyService:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def anonymize_expired(self, today: date | None = None) -> dict:
        today = today or today_msk()
        cutoff = date_months_ago(today, months=2)
        stats = {"bookings": 0, "users": 0}

        rows = await self.db.fetchall(
            "SELECT b.id, b.client_id FROM bookings b "
            "JOIN booking_slots s ON s.booking_id = b.id "
            "WHERE s.date < ? AND b.is_anonymized = 0",
            (cutoff.isoformat(),),
        )
        if rows:
            async with self.db.transaction():
                for row in rows:
                    await self.db.execute(
                        "UPDATE bookings SET client_name = ?, client_phone = NULL, "
                        "is_anonymized = 1 WHERE id = ?",
                        (ANONYMIZED_NAME, row["id"]),
                    )
            stats["bookings"] = len(rows)

        # Пользователи без записей в окне: имя и телефон очищаются
        cursor = await self.db.execute(
            "UPDATE users SET name = NULL, phone = NULL WHERE id IN ("
            "  SELECT u.id FROM users u"
            "  WHERE u.name IS NOT NULL OR u.phone IS NOT NULL"
            "  EXCEPT SELECT b.client_id FROM bookings b"
            "  JOIN booking_slots s ON s.booking_id = b.id"
            "  WHERE s.date >= ? AND b.client_id IS NOT NULL"
            ")",
            (cutoff.isoformat(),),
        )
        stats["users"] = cursor.rowcount
        logger.info(
            "Анонимизация ПД (окно до %s): записей %d, профилей %d",
            cutoff, stats["bookings"], stats["users"],
        )
        return stats
