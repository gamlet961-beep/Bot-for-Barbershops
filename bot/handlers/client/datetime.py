"""Дата и время — режим «Клиент» (ТЗ, п. 5.3; A.5.4–A.5.6).

Текущая календарная неделя пн–вс: доступны только дни со свободными слотами;
«Записаться на другую дату» — в пределах горизонта; при отсутствии слотов —
«Нет доступных записей».
"""

from datetime import date, time

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.config import Settings
from bot.db.database import Database
from bot.db.repositories.bookings import BookingsRepo
from bot.db.repositories.schedule import ScheduleRepo
from bot.keyboards import ClientMenu
from bot.keyboards.client import week_keyboard
from bot.services.scheduling import (
    NO_SLOTS_TEXT,
    available_slots,
    generate_slots,
    slot_key,
    within_horizon,
)
from bot.states.client_states import ClientBookingStates
from bot.utils import formatting as fmt
from bot.utils.dates import (
    WEEKDAY_SHORT,
    fmt_date_weekday,
    parse_user_date,
    today_msk,
    week_days,
)

router = Router(name="client_datetime")

OTHER_DATE_HINT = "Введите дату в формате ДД.ММ или ДД.ММ.ГГГГ, например: 05.12"


async def _day_free_slots(db: Database, day: date) -> list[tuple[time, time]]:
    schedule = ScheduleRepo(db)
    rule = await schedule.resolve_working_day(day)
    taken = await BookingsRepo(db).active_slot_starts(day)
    blocked = await schedule.blocked_slot_starts(day)
    from bot.utils.dates import now_msk

    return available_slots(day, rule, taken, blocked, now_msk())


async def _send_week(message: Message, db: Database) -> None:
    """Календарь текущей недели: только дни со свободными слотами."""
    today = today_msk()
    options: list[tuple[date, str, int]] = []
    for day in week_days(today):
        if day < today:
            continue
        slots = await _day_free_slots(db, day)
        if slots:
            label = f"{WEEKDAY_SHORT[day.weekday()]} {day.strftime('%d.%m')}"
            options.append((day, label, len(slots)))
    lines = ["<b>Дата и время</b> — текущая неделя:"]
    if not options:
        lines.append(NO_SLOTS_TEXT + " Попробуйте другую дату.")
    else:
        lines.append("Свободные слоты есть в дни:")
    builder = InlineKeyboardBuilder()
    for day, label, count in options:
        builder.button(text=f"{label} ({count})", callback_data=f"cb:day:{day.isoformat()}")
    builder.button(text="🗓 Записаться на другую дату", callback_data="cb:other")
    builder.button(text="⬅️ К услугам", callback_data="cb:backsvc")
    builder.adjust(3, 3, 1, 1)
    await message.answer("\n".join(lines), reply_markup=builder.as_markup())


async def _send_slots(message: Message, db: Database, day: date) -> None:
    slots = await _day_free_slots(db, day)
    if not slots:
        await message.answer(
            f"{fmt_date_weekday(day)}: {NO_SLOTS_TEXT} Выберите другой день."
        )
        await _send_week(message, db)
        return
    lines = [f"<b>{fmt_date_weekday(day)}</b> — свободные слоты:"]
    builder = InlineKeyboardBuilder()
    for slot_start, slot_end in slots:
        builder.button(
            text=f"{slot_start:%H:%M}–{slot_end:%H:%M}",
            callback_data=f"cb:slot:{slot_start.strftime('%H%M')}",
        )
    builder.button(text="⬅️ К календарю", callback_data="cb:week")
    builder.adjust(3, 2, 1)
    await message.answer("\n".join(lines), reply_markup=builder.as_markup())


async def open_datetime_internal(message: Message, db: Database, state: FSMContext) -> None:
    """Общий вход: из меню «Дата и время» и после выбора услуг."""
    data = await state.get_data()
    if not data.get("service_ids"):
        await message.answer('Сначала выберите услуги — кнопка «Услуга».')
        return
    await state.set_state(ClientBookingStates.choosing_datetime)
    await _send_week(message, db)


@router.message(F.text == ClientMenu.DATETIME, StateFilter(None))
async def open_datetime(message: Message, db: Database, state: FSMContext) -> None:
    await open_datetime_internal(message, db, state)


@router.callback_query(
    F.data == "cb:nextdate", StateFilter(ClientBookingStates.choosing_services)
)
async def cb_next_date(callback: CallbackQuery, db: Database, state: FSMContext) -> None:
    data = await state.get_data()
    if not data.get("service_ids"):
        await callback.answer("Сначала выберите хотя бы одну услугу.", show_alert=True)
        return
    await state.set_state(ClientBookingStates.choosing_datetime)
    if isinstance(callback.message, Message):
        await _send_week(callback.message, db)
    await callback.answer()


@router.callback_query(
    F.data == "cb:backsvc", StateFilter(ClientBookingStates.choosing_datetime)
)
async def cb_back_services(callback: CallbackQuery, db: Database, state: FSMContext) -> None:
    await state.set_state(ClientBookingStates.choosing_services)
    if isinstance(callback.message, Message):
        await _send_categories_light(callback.message, db, state)
    await callback.answer()


async def _send_categories_light(message: Message, db: Database, state: FSMContext) -> None:
    """Возврат к выбору услуг без сброса уже выбранных."""
    from bot.db.repositories.services import ServicesRepo
    from bot.keyboards.client import categories_keyboard

    data = await state.get_data()
    selected_ids: list[int] = data.get("service_ids", [])
    repo = ServicesRepo(db)
    categories = await repo.categories()
    grouped = await repo.active_by_category()
    counts = {
        cat_id: sum(1 for s in services if s["id"] in selected_ids)
        for cat_id, services in grouped.items()
    }
    chosen = [s for services in grouped.values() for s in services if s["id"] in selected_ids]
    lines = ["<b>Выберите услуги</b> (можно несколько):"]
    if chosen:
        lines.append("Выбрано: " + ", ".join(f"{fmt.esc(s['name'])} ({s['price']} ₽)" for s in chosen))
    lines.append("\nКатегория услуг:")
    await message.answer(
        "\n".join(lines), reply_markup=categories_keyboard(categories, selected_ids, counts)
    )


@router.callback_query(
    F.data == "cb:week", StateFilter(ClientBookingStates.choosing_datetime)
)
async def cb_week(callback: CallbackQuery, db: Database) -> None:
    if isinstance(callback.message, Message):
        await _send_week(callback.message, db)
    await callback.answer()


@router.callback_query(
    F.data.startswith("cb:day:"), StateFilter(ClientBookingStates.choosing_datetime)
)
async def cb_day(callback: CallbackQuery, db: Database, state: FSMContext) -> None:
    day = date.fromisoformat(callback.data.split(":")[2])
    await state.update_data(day=day.isoformat())
    if isinstance(callback.message, Message):
        await _send_slots(callback.message, db, day)
    await callback.answer()


@router.callback_query(
    F.data == "cb:other", StateFilter(ClientBookingStates.choosing_datetime)
)
async def cb_other(callback: CallbackQuery) -> None:
    if isinstance(callback.message, Message):
        await callback.message.answer(OTHER_DATE_HINT + "\nОтмена — /start")
    await callback.answer()


@router.message(ClientBookingStates.choosing_datetime, F.text)
async def other_date_input(
    message: Message, db: Database, state: FSMContext, config: Settings
) -> None:
    day = parse_user_date(message.text or "", today_msk())
    if day is None:
        await message.answer(f"Не понял дату. {OTHER_DATE_HINT}")
        return
    if not within_horizon(day, today_msk(), config.BOOKING_HORIZON_DAYS):
        from datetime import timedelta

        last_day = today_msk() + timedelta(days=config.BOOKING_HORIZON_DAYS)
        await message.answer(
            f"Дата вне горизонта записи. Доступны даты до {last_day.strftime('%d.%m.%Y')} включительно."
        )
        return
    await state.update_data(day=day.isoformat())
    await _send_slots(message, db, day)


@router.callback_query(
    F.data.startswith("cb:slot:"), StateFilter(ClientBookingStates.choosing_datetime)
)
async def cb_slot(callback: CallbackQuery, db: Database, state: FSMContext) -> None:
    """Слот выбран → корзина «К записи» (данные сохраняются в FSM)."""
    data = await state.get_data()
    day = date.fromisoformat(data["day"])
    hhmm = callback.data.split(":")[2]
    slot_start = time(int(hhmm[:2]), int(hhmm[2:]))
    slots = await _day_free_slots(db, day)
    match = next(
        ((s, e) for s, e in slots if s.hour == slot_start.hour and s.minute == slot_start.minute),
        None,
    )
    if match is None:
        await callback.answer(NO_SLOTS_TEXT, show_alert=True)
        if isinstance(callback.message, Message):
            await _send_slots(callback.message, db, day)
        return
    slot_start, slot_end = match
    await state.update_data(
        day=day.isoformat(),
        slot_start=slot_key(slot_start),
        slot_end=slot_key(slot_end),
    )
    await state.set_state(None)
    from bot.handlers.client.cart import send_cart

    if isinstance(callback.message, Message):
        await send_cart(callback.message, db, state)
    await callback.answer()
