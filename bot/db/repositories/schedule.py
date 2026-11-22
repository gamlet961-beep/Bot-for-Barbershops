"""Репозиторий расписания: шаблоны недели, исключения на даты, блокировки слотов.

Мутирующие методы выполняются в транзакции вызывающего слоя (сервисов),
сами по себе они не коммитят.
"""

from datetime import date, time

from bot.db.database import Database
from bot.services.scheduling import DayRule
from bot.utils.dates import parse_hhmm


def _row_to_rule(row) -> DayRule:
    if row is None or not row["is_working_day"]:
        return DayRule.off()
    return DayRule(
        is_working=True,
        start=parse_hhmm(row["start_time"]) if row["start_time"] else None,
        end=parse_hhmm(row["end_time"]) if row["end_time"] else None,
    )


class ScheduleRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    # ── шаблоны недели ──

    async def templates(self) -> dict[int, DayRule]:
        rows = await self.db.fetchall(
            "SELECT weekday, is_working_day, start_time, end_time FROM schedule_templates"
        )
        return {row["weekday"]: _row_to_rule(row) for row in rows}

    async def upsert_template(self, weekday: int, rule: DayRule) -> None:
        await self.db.execute(
            "INSERT INTO schedule_templates (weekday, is_working_day, start_time, end_time) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(weekday) DO UPDATE SET is_working_day = excluded.is_working_day, "
            "start_time = excluded.start_time, end_time = excluded.end_time",
            (
                weekday,
                int(rule.is_working),
                rule.start.strftime("%H:%M") if rule.start else None,
                rule.end.strftime("%H:%M") if rule.end else None,
            ),
        )

    # ── исключения на дату ──

    async def exceptions(self) -> dict[str, DayRule]:
        rows = await self.db.fetchall(
            "SELECT date, is_working_day, start_time, end_time FROM schedule_exceptions"
        )
        return {row["date"]: _row_to_rule(row) for row in rows}

    async def future_exceptions(self, from_date: date) -> list:
        return await self.db.fetchall(
            "SELECT date, is_working_day, start_time, end_time, note "
            "FROM schedule_exceptions WHERE date >= ? ORDER BY date",
            (from_date.isoformat(),),
        )

    async def get_exception(self, day: date):
        return await self.db.fetchone(
            "SELECT date, is_working_day, start_time, end_time, note "
            "FROM schedule_exceptions WHERE date = ?",
            (day.isoformat(),),
        )

    async def upsert_exception(self, day: date, rule: DayRule, note: str | None = None) -> None:
        await self.db.execute(
            "INSERT INTO schedule_exceptions (date, is_working_day, start_time, end_time, note) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(date) DO UPDATE SET is_working_day = excluded.is_working_day, "
            "start_time = excluded.start_time, end_time = excluded.end_time, note = excluded.note",
            (
                day.isoformat(),
                int(rule.is_working),
                rule.start.strftime("%H:%M") if rule.start else None,
                rule.end.strftime("%H:%M") if rule.end else None,
                note,
            ),
        )

    async def delete_exception(self, day: date) -> bool:
        cursor = await self.db.execute(
            "DELETE FROM schedule_exceptions WHERE date = ?", (day.isoformat(),)
        )
        return cursor.rowcount > 0

    # ── блокировки слотов ──

    async def blocked_slot_starts(self, day: date) -> set[str]:
        rows = await self.db.fetchall(
            "SELECT slot_start FROM blocked_slots WHERE date = ?", (day.isoformat(),)
        )
        return {row["slot_start"] for row in rows}

    async def blocked_slots(self, day: date) -> list:
        return await self.db.fetchall(
            "SELECT slot_start, slot_end, reason FROM blocked_slots "
            "WHERE date = ? ORDER BY slot_start",
            (day.isoformat(),),
        )

    async def dates_with_blocks(self, from_date: date) -> list:
        return await self.db.fetchall(
            "SELECT date, COUNT(*) AS cnt FROM blocked_slots WHERE date >= ? "
            "GROUP BY date ORDER BY date",
            (from_date.isoformat(),),
        )

    async def block_slot(self, day: date, slot_start: time, slot_end: time, reason: str | None = None) -> None:
        await self.db.execute(
            "INSERT INTO blocked_slots (date, slot_start, slot_end, reason) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(date, slot_start) DO UPDATE SET slot_end = excluded.slot_end, "
            "reason = excluded.reason",
            (day.isoformat(), slot_start.strftime("%H:%M"), slot_end.strftime("%H:%M"), reason),
        )

    async def unblock_slot(self, day: date, slot_start: time) -> bool:
        cursor = await self.db.execute(
            "DELETE FROM blocked_slots WHERE date = ? AND slot_start = ?",
            (day.isoformat(), slot_start.strftime("%H:%M")),
        )
        return cursor.rowcount > 0

    # ── композиция с ядром домена ──

    async def resolve_working_day(self, day: date) -> DayRule:
        from bot.services.scheduling import resolve_working_day as resolve

        templates = await self.templates()
        exceptions = await self.exceptions()
        return resolve(day, templates, exceptions)
