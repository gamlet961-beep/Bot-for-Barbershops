"""Раздел «Записи» — режим «Админ» (ТЗ, п. 4.4; A.4.1–A.4.4).

Фильтры: сегодня / завтра / произвольная дата / диапазон / все предстоящие;
поиск по имени и телефону; детали записи; отмена с уведомлением клиента.
Сюда же — вход в ручное создание записи (п. 4.5).
"""

from datetime import date, timedelta

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.config import Settings
from bot.db.database import Database
from bot.db.repositories.bookings import BookingsRepo
from bot.filters.is_admin import IsAdmin
from bot.keyboards import AdminMenu
from bot.services.booking import BookingService
from bot.states.admin_states import BookingsStates
from bot.utils import formatting as fmt
from bot.utils.dates import (
    WEEKDAY_SHORT,
    fmt_date,
    fmt_date_weekday,
    parse_user_date,
    today_msk,
)

router = Router(name="admin_bookings")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())

DATE_HINT = "Введите дату в формате ДД.ММ или ДД.ММ.ГГГГ (например, 05.12)."


async def _edit(callback: CallbackQuery, text: str, keyboard: InlineKeyboardMarkup | None = None) -> None:
    if isinstance(callback.message, Message):
        await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer()


def _bookings_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="📅 Сегодня", callback_data="bkg:today")
    builder.button(text="📅 Завтра", callback_data="bkg:tomorrow")
    builder.button(text="📅 Конкретная дата", callback_data="bkg:date")
    builder.button(text="📅 Диапазон дат", callback_data="bkg:range")
    builder.button(text="🔎 Поиск по имени/телефону", callback_data="bkg:search")
    builder.button(text="🗂 Все предстоящие", callback_data="bkg:upcoming")
    builder.button(text="➕ Создать запись вручную", callback_data="mnl:start")
    builder.button(text="⬅️ Меню админа", callback_data="bkg:menu")
    builder.adjust(2, 2, 2, 1, 1)
    return builder.as_markup()


async def _booking_line(db: Database, row) -> str:
    day = date.fromisoformat(row["slot_date"])
    mark = ""
    if day < today_msk():
        mark = " · прошла"
    elif day == today_msk():
        mark = " · сегодня"
    services = await db.fetchall(
        "SELECT name_at_booking FROM booking_services WHERE booking_id = ? ORDER BY id",
        (row["id"],),
    )
    services_line = ", ".join(s["name_at_booking"] for s in services) or "—"
    return (
        f"• {WEEKDAY_SHORT[day.weekday()]} {day.strftime('%d.%m')} {row['slot_start']} — "
        f"{fmt.esc(row['client_name'] or '—')} ({fmt.esc(row['client_phone'] or '—')}) · "
        f"{fmt.esc(services_line)} · {row['total_price']} ₽{mark}"
    )


async def _send_list(message: Message, db: Database, rows: list, title: str) -> None:
    if not rows:
        await message.answer(f"{title}\nЗаписей нет.")
        return
    lines = [f"<b>{title}</b>"]
    for row in rows[:20]:
        lines.append(await _booking_line(db, row))
    if len(rows) > 20:
        lines.append(f"… и ещё {len(rows) - 20} (уточните фильтр)")
    builder = InlineKeyboardBuilder()
    for row in rows[:20]:
        day = date.fromisoformat(row["slot_date"])
        builder.button(
            text=f"{day.strftime('%d.%m')} {row['slot_start']} · {row['client_name'] or '—'}",
            callback_data=f"bkg:one:{row['id']}",
        )
    builder.button(text="⬅️ К фильтрам", callback_data="bkg:open")
    builder.adjust(1)
    await message.answer("\n".join(lines), reply_markup=builder.as_markup())


@router.message(F.text == AdminMenu.BOOKINGS)
async def open_bookings(message: Message, db: Database, state: FSMContext) -> None:
    await state.clear()
    today = today_msk()
    rows = await BookingsRepo(db).in_range(today, today)
    await _send_list(message, db, rows, "Записи на сегодня")


@router.callback_query(F.data == "bkg:open")
async def cb_open(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await _edit(callback, "Раздел «Записи». Выберите фильтр:", _bookings_menu())


@router.callback_query(F.data == "bkg:menu")
async def cb_menu(callback: CallbackQuery, db: Database, state: FSMContext) -> None:
    await state.clear()
    from bot.db.repositories.admins import AdminsRepo
    from bot.keyboards.admin import admin_menu

    if isinstance(callback.message, Message):
        is_primary = await AdminsRepo(db).is_primary(callback.from_user.id)
        await callback.message.answer(
            "Меню администратора. Разделы: расписание, услуги, записи.",
            reply_markup=admin_menu(is_primary),
        )
    await callback.answer()


@router.callback_query(F.data == "bkg:today")
async def cb_today(callback: CallbackQuery, db: Database, state: FSMContext) -> None:
    await state.clear()
    today = today_msk()
    rows = await BookingsRepo(db).in_range(today, today)
    if isinstance(callback.message, Message):
        await _send_list(callback.message, db, rows, "Записи на сегодня")


@router.callback_query(F.data == "bkg:tomorrow")
async def cb_tomorrow(callback: CallbackQuery, db: Database, state: FSMContext) -> None:
    await state.clear()
    tomorrow = today_msk() + timedelta(days=1)
    rows = await BookingsRepo(db).in_range(tomorrow, tomorrow)
    if isinstance(callback.message, Message):
        await _send_list(callback.message, db, rows, "Записи на завтра")


@router.callback_query(F.data == "bkg:upcoming")
async def cb_upcoming(callback: CallbackQuery, db: Database, state: FSMContext, config: Settings) -> None:
    await state.clear()
    today = today_msk()
    rows = await BookingsRepo(db).in_range(today, today + timedelta(days=config.BOOKING_HORIZON_DAYS))
    if isinstance(callback.message, Message):
        await _send_list(callback.message, db, rows, "Все предстоящие записи")


@router.callback_query(F.data == "bkg:date")
async def cb_date(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(BookingsStates.waiting_date)
    if isinstance(callback.message, Message):
        await callback.message.answer(f"На какую дату показать записи?\n{DATE_HINT}")
    await callback.answer()


@router.message(BookingsStates.waiting_date)
async def input_date(message: Message, db: Database, state: FSMContext) -> None:
    day = parse_user_date(message.text or "", today_msk())
    if day is None:
        # для истории показываем и прошедшие даты: парсим без ограничения будущего
        await message.answer(DATE_HINT)
        return
    await state.clear()
    rows = await BookingsRepo(db).in_range(day, day)
    await _send_list(message, db, rows, f"Записи на {fmt_date(day)}")


@router.callback_query(F.data == "bkg:range")
async def cb_range(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(BookingsStates.waiting_range_from)
    if isinstance(callback.message, Message):
        await callback.message.answer(
            f"Диапазон дат. Начальная дата?\n{DATE_HINT}"
        )
    await callback.answer()


def _parse_any_date(text: str) -> date | None:
    """Для фильтров допустимы и прошедшие даты (история)."""
    import re

    match = re.fullmatch(r"(\d{1,2})\.(\d{1,2})(?:\.(\d{4}))?", text.strip())
    if not match:
        return None
    day, month = int(match.group(1)), int(match.group(2))
    year = int(match.group(3)) if match.group(3) else today_msk().year
    try:
        return date(year, month, day)
    except ValueError:
        return None


@router.message(BookingsStates.waiting_range_from)
async def input_range_from(message: Message, state: FSMContext) -> None:
    day = _parse_any_date(message.text or "")
    if day is None:
        await message.answer(DATE_HINT)
        return
    await state.update_data(range_from=day.isoformat())
    await state.set_state(BookingsStates.waiting_range_to)
    await message.answer(f"Начало: {fmt_date(day)}. Конечная дата?")


@router.message(BookingsStates.waiting_range_to)
async def input_range_to(message: Message, db: Database, state: FSMContext) -> None:
    day = _parse_any_date(message.text or "")
    if day is None:
        await message.answer(DATE_HINT)
        return
    data = await state.get_data()
    from_day = date.fromisoformat(data["range_from"])
    await state.clear()
    if day < from_day:
        await message.answer("Конечная дата раньше начальной. Попробуйте ещё раз.")
        return
    rows = await BookingsRepo(db).in_range(from_day, day)
    await _send_list(message, db, rows, f"Записи с {fmt_date(from_day)} по {fmt_date(day)}")


@router.callback_query(F.data == "bkg:search")
async def cb_search(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(BookingsStates.waiting_search)
    if isinstance(callback.message, Message):
        await callback.message.answer(
            "Введите имя (часть) или телефон (часть номера) для поиска записей."
        )
    await callback.answer()


@router.message(BookingsStates.waiting_search)
async def input_search(message: Message, db: Database, state: FSMContext) -> None:
    query = (message.text or "").strip()
    if len(query) < 2:
        await message.answer("Введите минимум 2 символа для поиска.")
        return
    await state.clear()
    rows = await BookingsRepo(db).search(query)
    await _send_list(message, db, rows, f"Поиск: «{fmt.esc(query)}»")


# ── детали записи и отмена ──

@router.callback_query(F.data.startswith("bkg:one:"))
async def cb_one(callback: CallbackQuery, db: Database) -> None:
    booking_id = int(callback.data.split(":")[2])
    view = await BookingsRepo(db).get_view(booking_id)
    if view is None:
        await callback.answer("Запись не найдена.", show_alert=True)
        return
    row = await BookingsRepo(db).get(booking_id)
    services = await db.fetchall(
        "SELECT name_at_booking, price_at_booking FROM booking_services WHERE booking_id = ? ORDER BY id",
        (booking_id,),
    )
    lines = [
        f"<b>Запись #{booking_id}</b>",
        f"Клиент: {fmt.esc(view['name'])}, {fmt.esc(view['phone'])}",
        f"Дата: {fmt.esc(view['date_weekday'])}, время: {fmt.esc(view['slot'])}",
        "Услуги: " + ", ".join(f"{fmt.esc(s['name_at_booking'])} ({s['price_at_booking']} ₽)" for s in services),
        f"Сумма: {view['total']} ₽",
        f"Создал: {'администратор' if row['created_by'] == 'admin' else 'клиент'}",
        f"Согласие на ПД: {'есть' if row['pd_consent_at'] else 'не требуется (ручная запись)'}",
        f"Статус: {row['status']}",
    ]
    builder = InlineKeyboardBuilder()
    if row["status"] == "active":
        builder.button(text="🗑 Отменить запись", callback_data=f"bkg:cancel:{booking_id}")
    builder.button(text="⬅️ К фильтрам", callback_data="bkg:open")
    builder.adjust(1)
    await _edit(callback, "\n".join(lines), builder.as_markup())


@router.callback_query(F.data.startswith("bkg:cancel:"))
async def cb_cancel(callback: CallbackQuery, db: Database) -> None:
    booking_id = int(callback.data.split(":")[2])
    view = await BookingsRepo(db).get_view(booking_id)
    if view is None:
        await callback.answer("Запись не найдена.", show_alert=True)
        return
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Да, отменить", callback_data=f"bkg:cyes:{booking_id}")
    builder.button(text="❌ Нет", callback_data=f"bkg:one:{booking_id}")
    builder.adjust(2)
    await _edit(
        callback,
        f"Отменить запись #{booking_id}?\n"
        f"{fmt.esc(view['name'])}, {fmt.esc(view['date_weekday'])} {fmt.esc(view['slot'])}. "
        "Клиент получит уведомление, слот освободится.",
        builder.as_markup(),
    )


@router.callback_query(F.data.startswith("bkg:cyes:"))
async def cb_cancel_yes(
    callback: CallbackQuery,
    db: Database,
    state: FSMContext,
    booking_service: BookingService,
) -> None:
    booking_id = int(callback.data.split(":")[2])
    cancelled = await booking_service.cancel_booking(
        booking_id, initiator="admin", reason="отменена администратором"
    )
    if isinstance(callback.message, Message):
        if cancelled:
            await callback.message.answer(
                f"✅ Запись #{booking_id} отменена. Клиент уведомлён, слот освобождён."
            )
        else:
            await callback.message.answer("Запись уже не активна.")
        await callback.message.answer("Раздел «Записи». Выберите фильтр:", reply_markup=_bookings_menu())
    await callback.answer()
