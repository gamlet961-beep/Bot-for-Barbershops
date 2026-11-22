"""Работа со временем: единый часовой пояс Europe/Moscow (ТЗ, п. 1).

Соглашения хранения:
- даты — календарные строки ISO ``YYYY-MM-DD`` (локальные, MSK);
- времена — строки ``HH:MM`` (локальные, MSK);
- метки времени (created_at, sent_at, ...) — UTC ISO-8601.
"""

import calendar
import re
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

MSK = ZoneInfo("Europe/Moscow")

WEEKDAY_NAMES = [
    "Понедельник", "Вторник", "Среда", "Четверг",
    "Пятница", "Суббота", "Воскресенье",
]
WEEKDAY_SHORT = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]


def now_msk() -> datetime:
    return datetime.now(MSK)


def today_msk() -> date:
    return now_msk().date()


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def combine_msk(d: date, t: time) -> datetime:
    """Календарная дата + время в MSK -> aware datetime."""
    return datetime.combine(d, t, tzinfo=MSK)


def fmt_date(d: date) -> str:
    return d.strftime("%d.%m.%Y")


def fmt_date_weekday(d: date) -> str:
    return f"{fmt_date(d)} ({WEEKDAY_NAMES[d.weekday()].lower()})"


def fmt_slot(slot_start: time, slot_end: time) -> str:
    return f"{slot_start:%H:%M}–{slot_end:%H:%M}"


def parse_hhmm(value: str) -> time | None:
    """«10:00» / «9:30» -> time; иначе None."""
    match = re.fullmatch(r"(\d{1,2}):(\d{2})", value.strip())
    if not match:
        return None
    hours, minutes = int(match.group(1)), int(match.group(2))
    if hours > 23 or minutes > 59:
        return None
    return time(hours, minutes)


def parse_user_date(value: str, today: date) -> date | None:
    """«21.11» / «21.11.2026» -> будущая дата; прошедшие и невалидные -> None."""
    match = re.fullmatch(r"(\d{1,2})\.(\d{1,2})(?:\.(\d{4}))?", value.strip())
    if not match:
        return None
    day, month = int(match.group(1)), int(match.group(2))
    year = int(match.group(3)) if match.group(3) else today.year
    try:
        parsed = date(year, month, day)
    except ValueError:
        return None
    if parsed < today:
        return None
    return parsed


def date_months_ago(d: date, months: int) -> date:
    """Дата на `months` месяцев назад (день месяца урезается по длине месяца)."""
    year, month = d.year, d.month - months
    while month <= 0:
        month += 12
        year -= 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def week_days(base: date) -> list[date]:
    """Дни текущей календарной недели (пн–вс), на которую приходится base."""
    monday = base - timedelta(days=base.weekday())
    return [monday + timedelta(days=i) for i in range(7)]
