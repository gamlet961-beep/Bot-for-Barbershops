"""Фаза 5: шаблоны сообщений — точные тексты ТЗ (п. 5.6, 4.6; A.5.11, A.7.1)."""

from datetime import date, time

from bot.utils import formatting as fmt
from bot.utils.dates import fmt_date_weekday


def make_row(**overrides):
    """Строка записи + слот по умолчанию (2026-11-25, среда, 18:00–20:00)."""
    row = {
        "id": 7,
        "client_name": "Иван",
        "client_phone": "+79260001122",
        "client_tg_id": 333,
        "slot_date": "2026-11-25",
        "slot_start": "18:00",
        "slot_end": "20:00",
        "total_price": 3000,
        "status": "active",
        "created_by": "client",
        "pd_consent_at": "2026-11-21T10:00:00+00:00",
        "cancelled_at": None,
        "cancel_reason": None,
        "cancel_initiator": None,
        "client_id": 1,
    }
    row.update(overrides)
    return row


def make_services():
    return [
        {"name_at_booking": "Стрижка", "price_at_booking": 2000},
        {"name_at_booking": "Борода", "price_at_booking": 1000},
    ]


def test_view_fields():
    view = fmt.build_view(make_row(), make_services())
    assert view["name"] == "Иван"
    assert view["phone"] == "+79260001122"
    assert view["date_ru"] == "25.11.2026"
    assert view["date_weekday"] == "25.11.2026 (среда)"
    assert view["slot"] == "18:00–20:00"
    assert view["services_line"] == "Стрижка, Борода"
    assert view["total"] == 3000


def test_confirmation_exact_structure():
    """П. 5.6 ТЗ: успех, услуги+сумма, дата+день недели+время, напоминание за 2 ч."""
    view = fmt.build_view(make_row(), make_services())
    text = fmt.booking_confirmation(view)
    assert text.startswith("✅ Вы успешно записаны!")
    assert "Услуги: Стрижка, Борода, сумма: 3000 руб." in text
    assert "Дата: 25.11.2026 (среда), время: 18:00–20:00." in text
    assert "За 2 часа до визита придёт напоминание." in text


def test_admin_new_booking_contains_all_fields():
    """A.7.1: имя, телефон, дата, время, услуги, сумма."""
    view = fmt.build_view(make_row(), make_services())
    text = fmt.admin_new_booking(view)
    for expected in ("Иван", "+79260001122", "25.11.2026 (среда)", "18:00–20:00",
                     "Стрижка, Борода", "3000"):
        assert expected in text


def test_admin_formulas_exact():
    view = fmt.build_view(make_row(), make_services())
    assert fmt.admin_client_cancelled(view) == (
        "Клиент Иван отменил запись на 25.11.2026 в 18:00."
    )
    assert fmt.admin_reminder_1h(view) == (
        "⏰ Через час клиент Иван записан на Стрижка, Борода в 18:00."
    )
    assert fmt.admin_blocked_bot(view).startswith("🚫 Запись отменена, клиент заблокировал бота")


def test_html_escaping_of_user_data():
    row = make_row(client_name="<b>Хакер</b>")
    view = fmt.build_view(row, make_services())
    text = fmt.admin_new_booking(view)
    assert "<b>Хакер</b>" not in text
    assert "&lt;b&gt;Хакер&lt;/b&gt;" in text


def test_day_report_lists_cancelled():
    views = [fmt.build_view(make_row(), make_services())]
    text = fmt.admin_day_report("25.11.2026", views)
    assert "Отменено записей: 1" in text
    assert "18:00 — Иван, +79260001122" in text
