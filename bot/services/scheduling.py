"""Ядро домена: расписание и 2-часовые слоты (ТЗ, разделы 4.2 и 6).

Чистые функции без зависимости от aiogram — тестируются напрямую (фаза 2).
Все времена локальные MSK; см. bot/utils/dates.py.

Правила ТЗ:
- интервал обязан быть кратен 2 часам, иначе сообщение INTERVAL_ERROR_TEXT;
- слоты нарезаются от начала шагом 2 часа; неполный последний слот не создаётся;
- один слот = одна запись;
- исключение на дату сильнее шаблона недели; нет ни того, ни другого — выходной.
"""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
import re
from typing import Collection, Mapping

from bot.utils.dates import combine_msk, date_months_ago, parse_hhmm

SLOT_STEP = timedelta(hours=2)
CANCEL_MIN_HOURS = 4
MY_BOOKINGS_MONTHS = 2

INTERVAL_ERROR_TEXT = (
    "Интервал должен быть кратен 2 часам (например, 10:00–20:00). Измените время"
)
NO_SLOTS_TEXT = "Нет доступных записей"
SLOT_TAKEN_TEXT = "Слот занят, выберите другое время"
CANCEL_TOO_LATE_TEXT = (
    "До визита меньше 4 часов. Для отмены свяжитесь с барбером."
)


@dataclass(frozen=True)
class DayRule:
    """Правило одного дня: выходной либо рабочие часы."""

    is_working: bool
    start: time | None = None
    end: time | None = None

    @staticmethod
    def off() -> "DayRule":
        return DayRule(is_working=False)

    @staticmethod
    def work(start: time, end: time) -> "DayRule":
        return DayRule(is_working=True, start=start, end=end)


def validate_interval(start: time, end: time) -> bool:
    """Длительность интервала кратна 2 часам (п. 4.2 ТЗ)."""
    if start is None or end is None or end <= start:
        return False
    begin = datetime.combine(date.min, start)
    finish = datetime.combine(date.min, end)
    return (finish - begin) % SLOT_STEP == timedelta(0)


def generate_slots(start: time, end: time) -> list[tuple[time, time]]:
    """Нарезка на 2-часовые слоты; неполный последний слот отбрасывается."""
    begin = datetime.combine(date.min, start)
    finish = datetime.combine(date.min, end)
    slots: list[tuple[time, time]] = []
    cursor = begin
    while cursor + SLOT_STEP <= finish:
        slots.append((cursor.time(), (cursor + SLOT_STEP).time()))
        cursor += SLOT_STEP
    return slots


def resolve_working_day(
    day: date,
    templates: Mapping[int, DayRule],
    exceptions: Mapping[str, DayRule],
) -> DayRule:
    """Каскад: исключение на дату → шаблон дня недели → выходной."""
    exception = exceptions.get(day.isoformat())
    if exception is not None:
        return exception
    return templates.get(day.weekday(), DayRule.off())


def parse_hours_range(text: str) -> tuple[time, time] | None:
    """«10:00-20:00» / «10:00–20:00» / «10:00 20:00» -> (start, end) | None."""
    parts = re.split(r"[-–—\s]+", text.strip())
    if len(parts) != 2:
        return None
    start = parse_hhmm(parts[0])
    end = parse_hhmm(parts[1])
    if start is None or end is None:
        return None
    return start, end


def slot_key(t: time) -> str:
    """Ключ слота в БД: HH:MM."""
    return t.strftime("%H:%M")


def available_slots(
    day: date,
    rule: DayRule,
    taken: Collection[str],
    blocked: Collection[str],
    now: datetime,
) -> list[tuple[time, time]]:
    """Свободные слоты: сетка − активные записи − блокировки − уже начавшиеся."""
    if not rule.is_working or rule.start is None or rule.end is None:
        return []
    result: list[tuple[time, time]] = []
    for slot_start, slot_end in generate_slots(rule.start, rule.end):
        key = slot_key(slot_start)
        if key in taken or key in blocked:
            continue
        if combine_msk(day, slot_start) <= now:
            continue
        result.append((slot_start, slot_end))
    return result


def within_horizon(day: date, today: date, horizon_days: int) -> bool:
    """Дата записи в пределах горизонта (включительно; сегодня можно)."""
    return today <= day <= today + timedelta(days=horizon_days)


def can_cancel(visit_start: datetime, now: datetime) -> bool:
    """Отмена доступна при остатке не менее 4 часов (п. 5.7 ТЗ)."""
    return visit_start - now >= timedelta(hours=CANCEL_MIN_HOURS)


def my_bookings_window_start(today: date) -> date:
    """Начало окна «Мои записи»: 2 месяца назад (п. 5.7, 9.2 ТЗ)."""
    return date_months_ago(today, MY_BOOKINGS_MONTHS)


def split_for_display(bookings: list, today: date) -> tuple[list, list]:
    """Предстоящие (по возрастанию) и прошедшие (по убыванию) записи.

    Ожидает строки BOOKING_SELECT: slot_date (ISO) и slot_start (HH:MM).
    """
    upcoming = sorted(
        (b for b in bookings if b["slot_date"] >= today.isoformat()),
        key=lambda b: (b["slot_date"], b["slot_start"]),
    )
    past = sorted(
        (b for b in bookings if b["slot_date"] < today.isoformat()),
        key=lambda b: (b["slot_date"], b["slot_start"]),
        reverse=True,
    )
    return upcoming, past
