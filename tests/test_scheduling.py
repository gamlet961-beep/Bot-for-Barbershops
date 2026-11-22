"""Фаза 2: ядро расписания и слотов (чистые функции)."""

import sqlite3
from datetime import date, datetime, time

from bot.services.scheduling import (
    CANCEL_MIN_HOURS,
    DayRule,
    available_slots,
    can_cancel,
    generate_slots,
    my_bookings_window_start,
    resolve_working_day,
    split_for_display,
    slot_key,
    validate_interval,
    within_horizon,
)
from bot.utils.dates import MSK

T = time


# ── валидация кратности 2 часам (п. 4.2 ТЗ; A.2.3, UC-4) ──

def test_interval_valid_cases():
    assert validate_interval(T(10, 0), T(20, 0))
    assert validate_interval(T(10, 0), T(12, 0))
    assert validate_interval(T(10, 30), T(20, 30))  # кратна длительность
    assert validate_interval(T(0, 0), T(22, 0))


def test_interval_invalid_cases():
    assert not validate_interval(T(10, 0), T(19, 0))   # не кратно 2 ч
    assert not validate_interval(T(10, 0), T(19, 30))  # не кратно 2 ч
    assert not validate_interval(T(10, 0), T(10, 0))   # пустой
    assert not validate_interval(T(20, 0), T(10, 0))   # конец раньше начала
    assert not validate_interval(T(23, 0), T(1, 0))    # через полночь не поддерживается


# ── нарезка слотов (A.2.2) ──

def test_five_slots_for_ten_to_eight():
    slots = generate_slots(T(10, 0), T(20, 0))
    assert slots == [
        (T(10, 0), T(12, 0)), (T(12, 0), T(14, 0)), (T(14, 0), T(16, 0)),
        (T(16, 0), T(18, 0)), (T(18, 0), T(20, 0)),
    ]


def test_slots_from_half_hour_start():
    slots = generate_slots(T(10, 30), T(20, 30))
    assert slots[0] == (T(10, 30), T(12, 30))
    assert len(slots) == 5


def test_incomplete_last_slot_dropped():
    slots = generate_slots(T(10, 0), T(21, 0))  # 11 часов
    assert slots[-1] == (T(18, 0), T(20, 0))    # 20:00–21:00 не создаётся
    assert len(slots) == 5


# ── каскад «исключение → шаблон → выходной» (A.2.4) ──

def _rules() -> tuple[dict[int, DayRule], dict[str, DayRule]]:
    templates = {0: DayRule.work(T(10, 0), T(20, 0)), 1: DayRule.off()}
    exceptions = {"2026-11-25": DayRule.work(T(12, 0), T(18, 0))}
    return templates, exceptions


def test_template_used_when_no_exception():
    templates, exceptions = _rules()
    assert resolve_working_day(date(2026, 11, 23), templates, exceptions).is_working  # пн


def test_day_without_rules_is_off():
    templates, exceptions = _rules()
    rule = resolve_working_day(date(2026, 11, 24), templates, exceptions)  # вт: шаблон off
    assert not rule.is_working
    rule2 = resolve_working_day(date(2026, 11, 26), templates, exceptions)  # чт: нет правил
    assert not rule2.is_working


def test_exception_overrides_template():
    templates, exceptions = _rules()
    # 2026-11-25 — среда (шаблона нет, но есть исключение с работой)
    rule = resolve_working_day(date(2026, 11, 25), templates, exceptions)
    assert rule.is_working and rule.start == T(12, 0) and rule.end == T(18, 0)
    # исключение-выходной перекрывает рабочий шаблон
    exceptions = {"2026-11-23": DayRule.off()}
    assert not resolve_working_day(date(2026, 11, 23), templates, exceptions).is_working


# ── доступность слотов (A.5.4, A.5.6) ──

def test_available_slots_exclude_taken_blocked_past():
    rule = DayRule.work(T(10, 0), T(20, 0))
    day = date(2026, 11, 23)
    now = datetime(2026, 11, 23, 15, 0, tzinfo=MSK)
    taken = {slot_key(T(12, 0))}       # занято 12:00
    blocked = {slot_key(T(16, 0))}     # заблокировано 16:00
    slots = available_slots(day, rule, taken, blocked, now)
    starts = [slot_key(s) for s, _ in slots]
    # сетка 10,12,14,16,18; к 15:00 уже начались 10, 12, 14; 16 — заблокирован
    assert starts == ["18:00"]


def test_available_slots_full_day_free():
    rule = DayRule.work(T(10, 0), T(20, 0))
    day = date(2026, 12, 1)
    now = datetime(2026, 11, 20, 12, 0, tzinfo=MSK)
    slots = available_slots(day, rule, set(), set(), now)
    assert len(slots) == 5


def test_available_slots_off_day():
    day = date(2026, 12, 1)
    now = datetime(2026, 11, 20, 12, 0, tzinfo=MSK)
    assert available_slots(day, DayRule.off(), set(), set(), now) == []


# ── горизонт записи (A.5.5) ──

def test_within_horizon_boundaries():
    today = date(2026, 11, 21)
    assert within_horizon(today, today, 30)
    assert within_horizon(today + timedelta_days(30), today, 30)
    assert not within_horizon(today + timedelta_days(31), today, 30)
    assert not within_horizon(today - timedelta_days(1), today, 30)


def timedelta_days(n):
    from datetime import timedelta

    return timedelta(days=n)


# ── окно отмены 4 часа (A.6.4, A.6.5) ──

def test_can_cancel_boundaries():
    visit = datetime(2026, 11, 25, 18, 0, tzinfo=MSK)
    assert can_cancel(visit, datetime(2026, 11, 25, 14, 0, tzinfo=MSK))       # ровно 4 ч
    assert not can_cancel(visit, datetime(2026, 11, 25, 14, 1, tzinfo=MSK))   # 3 ч 59 мин
    assert can_cancel(visit, datetime(2026, 11, 20, 10, 0, tzinfo=MSK))
    assert CANCEL_MIN_HOURS == 4


# ── окно «Мои записи» 2 месяца (A.6.1, A.8.2) ──

def test_my_bookings_window():
    assert my_bookings_window_start(date(2026, 11, 21)) == date(2026, 9, 21)
    assert my_bookings_window_start(date(2026, 12, 31)) == date(2026, 10, 31)


def _booking_rows(pairs: list[tuple[str, str]]) -> list:
    """Строки как из BOOKING_SELECT: sqlite3.Row с колонкой slot_date.

    Обычный dict скрыл бы опечатку в имени ключа (Row бросает IndexError) —
    регрессия на инцидент с кнопкой «Мои записи».
    """
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT ? AS slot_date, ? AS slot_start", pairs[0]
    ).fetchall()
    for pair in pairs[1:]:
        rows += conn.execute(
            "SELECT ? AS slot_date, ? AS slot_start", pair
        ).fetchall()
    conn.close()
    return rows


def test_split_for_display_ordering():
    rows = _booking_rows(
        [
            ("2026-11-21", "18:00"),
            ("2026-11-05", "10:00"),
            ("2026-11-23", "12:00"),
            ("2026-11-01", "14:00"),
        ]
    )
    upcoming, past = split_for_display(rows, date(2026, 11, 21))
    assert [r["slot_date"] for r in upcoming] == ["2026-11-21", "2026-11-23"]
    assert [r["slot_date"] for r in past] == ["2026-11-05", "2026-11-01"]
