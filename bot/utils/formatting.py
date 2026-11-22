"""Шаблоны сообщений ТЗ и построение представления записи (view).

Все тексты с пользовательскими данными экранируются (parse_mode=HTML).
Тексты из ТЗ используются дословно: п. 4.2, 4.6, 5.6, 5.7.
"""

import html
from typing import Optional


def esc(value) -> str:
    return html.escape(str(value))


def build_view(row, services: list) -> dict:
    """Строка записи + услуги -> словарь для шаблонов."""
    from bot.utils.dates import fmt_date_weekday

    names = [s["name_at_booking"] for s in services]
    total = sum(s["price_at_booking"] for s in services)
    slot = f"{row['slot_start']}–{row['slot_end']}"
    date_iso = row["slot_date"]
    from datetime import date

    day = date.fromisoformat(date_iso)
    return {
        "id": row["id"],
        "name": row["client_name"] or "—",
        "phone": row["client_phone"] or "—",
        "client_tg_id": row["client_tg_id"],
        "date": date_iso,
        "date_ru": day.strftime("%d.%m.%Y"),
        "date_weekday": fmt_date_weekday(day),
        "slot": slot,
        "slot_start": row["slot_start"],
        "services": names,
        "services_line": ", ".join(names),
        "total": total if total else row["total_price"],
    }


# ── Клиенту ──

def booking_confirmation(view: dict) -> str:
    """Подтверждение записи (ТЗ, п. 5.6 — дословная структура)."""
    return (
        "✅ Вы успешно записаны!\n"
        f"Услуги: {esc(view['services_line'])}, сумма: {view['total']} руб.\n"
        f"Дата: {esc(view['date_weekday'])}, время: {esc(view['slot'])}.\n"
        "За 2 часа до визита придёт напоминание."
    )


def client_cancelled_by_admin(view: dict) -> str:
    return (
        f"Ваша запись на {esc(view['date_ru'])} в {esc(view['slot'])} отменена "
        "администратором. Свяжитесь с барбером для новой записи."
    )


def client_cancel_self(view: dict) -> str:
    """Подтверждение отмены самим клиентом (п. 5.7 ТЗ)."""
    return (
        f"✅ Ваша запись на {esc(view['date_ru'])} в {esc(view['slot'])} отменена. "
        "Слот освобождён. Запишитесь снова в любое время!"
    )


def client_day_cancelled(view: dict) -> str:
    return (
        f"Ваша запись на {esc(view['date_ru'])} в {esc(view['slot'])} отменена: "
        "рабочий день отменён. Свяжитесь с барбером для новой записи."
    )


def client_reminder(view: dict, hours: int) -> str:
    return (
        f"⏰ Напоминание: через {hours} ч — визит к барберу.\n"
        f"Дата: {esc(view['date_weekday'])}, время: {esc(view['slot'])}.\n"
        f"Услуги: {esc(view['services_line'])}."
    )


# ── Админам ──

def admin_new_booking(view: dict) -> str:
    """Мгновенное уведомление о записи клиента (ТЗ, п. 4.6)."""
    return (
        "🔔 Новая запись клиента\n"
        f"Имя: {esc(view['name'])}\n"
        f"Телефон: {esc(view['phone'])}\n"
        f"Дата: {esc(view['date_weekday'])}\n"
        f"Время: {esc(view['slot'])}\n"
        f"Услуги: {esc(view['services_line'])}\n"
        f"Сумма: {view['total']} руб."
    )


def admin_client_cancelled(view: dict) -> str:
    """Отмена клиентом (ТЗ, п. 4.6 — дословная формула)."""
    return (
        f"Клиент {esc(view['name'])} отменил запись "
        f"на {esc(view['date_ru'])} в {esc(view['slot_start'])}."
    )


def admin_reminder_1h(view: dict) -> str:
    """Напоминание админу за 1 час (ТЗ, п. 4.6 — дословная формула)."""
    return (
        f"⏰ Через час клиент {esc(view['name'])} записан "
        f"на {esc(view['services_line'])} в {esc(view['slot_start'])}."
    )


def admin_blocked_bot(view: dict) -> str:
    """Автоотмена при блокировке бота (ТЗ, п. 4.6, 7.3 — дословная формула + детали)."""
    return (
        "🚫 Запись отменена, клиент заблокировал бота\n"
        f"Клиент: {esc(view['name'])}, телефон: {esc(view['phone'])}\n"
        f"Запись: {esc(view['date_weekday'])} в {esc(view['slot'])}\n"
        f"Услуги: {esc(view['services_line'])}. Слот освобождён."
    )


def admin_day_report(day_ru: str, views: list[dict]) -> str:
    """Отчёт по отменённым записям при удалении рабочего дня (п. 4.2, 4.6 ТЗ)."""
    lines = [
        f"📋 Отчёт: рабочий день {esc(day_ru)} отменён. Отменено записей: {len(views)}."
    ]
    for view in views:
        lines.append(
            f"• {esc(view['slot_start'])} — {esc(view['name'])}, {esc(view['phone'])} "
            f"({esc(view['services_line'])}, {view['total']} руб.)"
        )
    return "\n".join(lines)


# ── Служебные ──

def slot_taken_text() -> str:
    from bot.services.scheduling import SLOT_TAKEN_TEXT

    return SLOT_TAKEN_TEXT


def no_slots_text() -> str:
    from bot.services.scheduling import NO_SLOTS_TEXT

    return NO_SLOTS_TEXT


def interval_error_text() -> str:
    from bot.services.scheduling import INTERVAL_ERROR_TEXT

    return INTERVAL_ERROR_TEXT


def cancel_too_late_text() -> str:
    from bot.services.scheduling import CANCEL_TOO_LATE_TEXT

    return CANCEL_TOO_LATE_TEXT
