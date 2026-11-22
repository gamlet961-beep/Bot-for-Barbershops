"""Редактор расписания — режим «Админ» (ТЗ, п. 4.2; A.2.1–A.2.8, UC-4, UC-8).

Разделы:
- шаблон недели (рабочий день / выходной / часы; валидация кратности 2 часам);
- исключения на дату (изменить часы / выходной или отпуск / удалить);
- блокировка и разблокировка отдельных слотов (только из сетки дня);
- отмена рабочего дня: при наличии записей — предупреждение Да/Нет,
  при «Да» — автоотмена, уведомления клиентам, отчёт админам.
"""

from datetime import date, time

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.db.database import Database
from bot.db.repositories.bookings import BookingsRepo
from bot.db.repositories.schedule import ScheduleRepo
from bot.filters.is_admin import IsAdmin
from bot.keyboards import AdminMenu
from bot.services.booking import BookingService
from bot.services.scheduling import (
    INTERVAL_ERROR_TEXT,
    DayRule,
    generate_slots,
    parse_hours_range,
    slot_key,
    validate_interval,
)
from bot.states.admin_states import ScheduleStates
from bot.utils.dates import (
    WEEKDAY_NAMES,
    WEEKDAY_SHORT,
    fmt_date,
    fmt_date_weekday,
    parse_user_date,
    today_msk,
)

router = Router(name="admin_schedule")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())

DATE_HINT = "Введите дату в формате ДД.ММ или ДД.ММ.ГГГГ (например, 05.12)."
HOURS_HINT = "Введите интервал часов, например: 10:00-20:00"


async def _edit(callback: CallbackQuery, text: str, keyboard: InlineKeyboardMarkup | None = None) -> None:
    if isinstance(callback.message, Message):
        await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer()


# ── главное меню раздела ──

def _schedule_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="📅 Шаблон недели", callback_data="sch:tpl")
    builder.button(text="🔁 Исключения на дату", callback_data="sch:exc")
    builder.button(text="🔒 Блокировка слотов", callback_data="sch:blk")
    builder.button(text="❌ Отменить рабочий день", callback_data="sch:cday")
    builder.adjust(1)
    return builder.as_markup()


@router.message(F.text == AdminMenu.SCHEDULE)
async def open_schedule(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Редактор расписания. Что настраиваем?", reply_markup=_schedule_menu())


@router.callback_query(F.data == "sch:menu")
async def cb_menu(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await _edit(callback, "Редактор расписания. Что настраиваем?", _schedule_menu())


# ── шаблон недели ──

def _templates_text(templates: dict[int, DayRule]) -> str:
    lines = ["<b>Шаблон недели:</b>"]
    for weekday in range(7):
        rule = templates.get(weekday, DayRule.off())
        if rule.is_working and rule.start and rule.end:
            hours = f"{rule.start:%H:%M}–{rule.end:%H:%M}"
        else:
            hours = "выходной"
        lines.append(f"• {WEEKDAY_NAMES[weekday]}: {hours}")
    return "\n".join(lines)


def _weekdays_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for weekday in range(7):
        builder.button(text=WEEKDAY_SHORT[weekday], callback_data=f"sch:tpl:{weekday}")
    builder.button(text="⬅️ Расписание", callback_data="sch:menu")
    builder.adjust(4, 3, 1)
    return builder.as_markup()


async def _send_templates(message: Message, db: Database) -> None:
    templates = await ScheduleRepo(db).templates()
    await message.answer(_templates_text(templates), reply_markup=_weekdays_keyboard())


@router.callback_query(F.data == "sch:tpl")
async def cb_tpl(callback: CallbackQuery, db: Database) -> None:
    templates = await ScheduleRepo(db).templates()
    await _edit(callback, _templates_text(templates), _weekdays_keyboard())


@router.callback_query(F.data.startswith("sch:tpl:"))
async def cb_tpl_day(callback: CallbackQuery, db: Database) -> None:
    weekday = int(callback.data.split(":")[2])
    templates = await ScheduleRepo(db).templates()
    rule = templates.get(weekday, DayRule.off())
    current = (
        f"{rule.start:%H:%M}–{rule.end:%H:%M}" if rule.is_working and rule.start and rule.end
        else "выходной"
    )
    builder = InlineKeyboardBuilder()
    builder.button(text="🔧 Рабочие часы", callback_data=f"sch:tplw:{weekday}")
    builder.button(text="🚫 Выходной", callback_data=f"sch:tplo:{weekday}")
    builder.button(text="⬅️ Шаблон недели", callback_data="sch:tpl")
    builder.adjust(1)
    await _edit(
        callback,
        f"<b>{WEEKDAY_NAMES[weekday]}</b>\nСейчас: {current}.\nЧто задаём?",
        builder.as_markup(),
    )


@router.callback_query(F.data.startswith("sch:tplw:"))
async def cb_tpl_work(callback: CallbackQuery, state: FSMContext) -> None:
    weekday = int(callback.data.split(":")[2])
    await state.update_data(tpl_weekday=weekday)
    await state.set_state(ScheduleStates.waiting_tpl_hours)
    if isinstance(callback.message, Message):
        await callback.message.answer(
            f"Рабочие часы для «{WEEKDAY_NAMES[weekday]}».\n{HOURS_HINT}\nОтмена — /menu"
        )
    await callback.answer()


@router.message(ScheduleStates.waiting_tpl_hours)
async def tpl_hours_input(message: Message, db: Database, state: FSMContext) -> None:
    data = await state.get_data()
    weekday = data["tpl_weekday"]
    parsed = parse_hours_range(message.text or "")
    if parsed is None:
        await message.answer(HOURS_HINT)
        return
    start, end = parsed
    if not validate_interval(start, end):
        # A.2.3: сообщение ТЗ во всех местах ввода часов
        await message.answer(INTERVAL_ERROR_TEXT)
        return
    async with db.transaction():
        await ScheduleRepo(db).upsert_template(weekday, DayRule.work(start, end))
    await state.clear()
    await message.answer(
        f"✅ {WEEKDAY_NAMES[weekday]}: рабочие часы {start:%H:%M}–{end:%H:%M}."
    )
    await _send_templates(message, db)


@router.callback_query(F.data.startswith("sch:tplo:"))
async def cb_tpl_off(callback: CallbackQuery, db: Database, state: FSMContext) -> None:
    weekday = int(callback.data.split(":")[2])
    async with db.transaction():
        await ScheduleRepo(db).upsert_template(weekday, DayRule.off())
    await state.clear()
    if isinstance(callback.message, Message):
        await callback.message.answer(f"✅ {WEEKDAY_NAMES[weekday]}: выходной.")
        await _send_templates(callback.message, db)
    await callback.answer()


# ── исключения на дату ──

def _exceptions_text(rows: list) -> str:
    lines = ["<b>Исключения на даты:</b>"]
    if not rows:
        lines.append("— пока нет —")
    for row in rows:
        if row["is_working_day"]:
            kind = f"часы {row['start_time']}–{row['end_time']}"
        else:
            kind = "выходной"
        note = f" ({row['note']})" if row["note"] else ""
        lines.append(f"• {fmt_date(date.fromisoformat(row['date']))} — {kind}{note}")
    return "\n".join(lines)


def _exceptions_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="➕ Добавить исключение", callback_data="sch:excadd")
    builder.button(text="🗑 Удалить исключение", callback_data="sch:excdel")
    builder.button(text="⬅️ Расписание", callback_data="sch:menu")
    builder.adjust(1)
    return builder.as_markup()


@router.callback_query(F.data == "sch:exc")
async def cb_exc(callback: CallbackQuery, db: Database) -> None:
    rows = await ScheduleRepo(db).future_exceptions(today_msk())
    await _edit(callback, _exceptions_text(rows), _exceptions_keyboard())


@router.callback_query(F.data == "sch:excadd")
async def cb_exc_add(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(ScheduleStates.waiting_exc_date)
    if isinstance(callback.message, Message):
        await callback.message.answer(
            f"На какую дату задаём исключение?\n{DATE_HINT}\nОтмена — /menu"
        )
    await callback.answer()


@router.message(ScheduleStates.waiting_exc_date)
async def exc_date_input(message: Message, db: Database, state: FSMContext) -> None:
    day = parse_user_date(message.text or "", today_msk())
    if day is None:
        await message.answer(DATE_HINT)
        return
    await state.update_data(exc_day=day.isoformat())
    await state.set_state(ScheduleStates.waiting_exc_action)
    builder = InlineKeyboardBuilder()
    builder.button(text="🔧 Изменить часы", callback_data="sch:exchours")
    builder.button(text="🚫 Выходной / отпуск", callback_data="sch:excoff")
    builder.button(text="⬅️ Расписание", callback_data="sch:menu")
    builder.adjust(1)
    await message.answer(
        f"Дата: <b>{fmt_date_weekday(day)}</b>. Что делаем с этим днём?",
        reply_markup=builder.as_markup(),
    )


@router.callback_query(F.data == "sch:exchours", ScheduleStates.waiting_exc_action)
async def cb_exc_hours(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(ScheduleStates.waiting_exc_hours)
    if isinstance(callback.message, Message):
        await callback.message.answer(f"Часы работы на эту дату.\n{HOURS_HINT}\nОтмена — /menu")
    await callback.answer()


@router.message(ScheduleStates.waiting_exc_hours)
async def exc_hours_input(message: Message, db: Database, state: FSMContext) -> None:
    parsed = parse_hours_range(message.text or "")
    if parsed is None:
        await message.answer(HOURS_HINT)
        return
    start, end = parsed
    if not validate_interval(start, end):
        await message.answer(INTERVAL_ERROR_TEXT)
        return
    data = await state.get_data()
    day = date.fromisoformat(data["exc_day"])
    async with db.transaction():
        await ScheduleRepo(db).upsert_exception(day, DayRule.work(start, end))
    await state.clear()
    await message.answer(
        f"✅ Исключение на {fmt_date(day)}: часы {start:%H:%M}–{end:%H:%M}."
    )
    rows = await ScheduleRepo(db).future_exceptions(today_msk())
    await message.answer(_exceptions_text(rows), reply_markup=_exceptions_keyboard())


@router.callback_query(F.data == "sch:excoff", ScheduleStates.waiting_exc_action)
async def cb_exc_off(callback: CallbackQuery, db: Database, state: FSMContext) -> None:
    data = await state.get_data()
    day = date.fromisoformat(data["exc_day"])
    async with db.transaction():
        await ScheduleRepo(db).upsert_exception(day, DayRule.off(), note="выходной")
    await state.clear()
    if isinstance(callback.message, Message):
        await callback.message.answer(f"✅ {fmt_date(day)} — выходной (исключение).")
        rows = await ScheduleRepo(db).future_exceptions(today_msk())
        await callback.message.answer(_exceptions_text(rows), reply_markup=_exceptions_keyboard())
    await callback.answer()


@router.callback_query(F.data == "sch:excdel")
async def cb_exc_del(callback: CallbackQuery, db: Database) -> None:
    rows = await ScheduleRepo(db).future_exceptions(today_msk())
    if not rows:
        await callback.answer("Исключений нет.", show_alert=True)
        return
    builder = InlineKeyboardBuilder()
    for row in rows:
        day = date.fromisoformat(row["date"])
        kind = (
            f"{row['start_time']}–{row['end_time']}" if row["is_working_day"] else "выходной"
        )
        builder.button(text=f"🗑 {fmt_date(day)} ({kind})", callback_data=f"sch:excdelete:{row['date']}")
    builder.button(text="⬅️ Расписание", callback_data="sch:menu")
    builder.adjust(1)
    await _edit(callback, "Какое исключение удалить? (день вернётся к шаблону недели)", builder.as_markup())


@router.callback_query(F.data.startswith("sch:excdelete:"))
async def cb_exc_delete_do(callback: CallbackQuery, db: Database, state: FSMContext) -> None:
    day = date.fromisoformat(callback.data.split(":")[2])
    async with db.transaction():
        removed = await ScheduleRepo(db).delete_exception(day)
    await state.clear()
    if isinstance(callback.message, Message):
        if removed:
            await callback.message.answer(
                f"✅ Исключение на {fmt_date(day)} удалено. Действует шаблон недели."
            )
        else:
            await callback.message.answer("Исключение не найдено.")
        rows = await ScheduleRepo(db).future_exceptions(today_msk())
        await callback.message.answer(_exceptions_text(rows), reply_markup=_exceptions_keyboard())
    await callback.answer()


# ── блокировка слотов ──

@router.callback_query(F.data == "sch:blk")
async def cb_blk(callback: CallbackQuery) -> None:
    builder = InlineKeyboardBuilder()
    builder.button(text="🔒 Заблокировать слот", callback_data="sch:blkadd")
    builder.button(text="🔓 Разблокировать", callback_data="sch:blkrm")
    builder.button(text="⬅️ Расписание", callback_data="sch:menu")
    builder.adjust(1)
    await _edit(
        callback,
        "Блокировка слотов. Блокировать можно только существующие слоты дня "
        "(2-часовая сетка). Занятый записью слот блокировать не нужно.",
        builder.as_markup(),
    )


@router.callback_query(F.data == "sch:blkadd")
async def cb_blk_add(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(ScheduleStates.waiting_block_date)
    if isinstance(callback.message, Message):
        await callback.message.answer(
            f"На какую дату блокируем слот?\n{DATE_HINT}\nОтмена — /menu"
        )
    await callback.answer()


async def _send_day_grid(message: Message, db: Database, day: date) -> None:
    """Сетка слотов дня с пометками и кнопками блокировки свободных слотов."""
    schedule = ScheduleRepo(db)
    rule = await schedule.resolve_working_day(day)
    if not rule.is_working or rule.start is None or rule.end is None:
        await message.answer(f"{fmt_date_weekday(day)} — нерабочий день, блокировать нечего.")
        return
    taken = await BookingsRepo(db).active_slot_starts(day)
    blocked = await schedule.blocked_slot_starts(day)
    lines = [f"<b>{fmt_date_weekday(day)}</b> — сетка слотов:"]
    builder = InlineKeyboardBuilder()
    for slot_start, slot_end in generate_slots(rule.start, rule.end):
        key = slot_key(slot_start)
        title = f"{key}–{slot_end:%H:%M}"
        if key in taken:
            lines.append(f"✖ {title} — занят записью")
        elif key in blocked:
            lines.append(f"🔒 {title} — заблокирован")
        else:
            lines.append(f"◻ {title} — свободен")
            builder.button(text=f"🔒 {key}", callback_data=f"sch:blkdo:{key}")
    builder.button(text="⬅️ Блокировки", callback_data="sch:blk")
    builder.adjust(4, 1)
    await message.answer("\n".join(lines), reply_markup=builder.as_markup())


@router.message(ScheduleStates.waiting_block_date)
async def blk_date_input(message: Message, db: Database, state: FSMContext) -> None:
    day = parse_user_date(message.text or "", today_msk())
    if day is None:
        await message.answer(DATE_HINT)
        return
    await state.update_data(block_day=day.isoformat())
    await state.set_state(ScheduleStates.waiting_block_slot)
    await _send_day_grid(message, db, day)


@router.callback_query(F.data.startswith("sch:blkdo:"), ScheduleStates.waiting_block_slot)
async def cb_blk_do(callback: CallbackQuery, db: Database, state: FSMContext) -> None:
    key = callback.data.split(":")[2]
    data = await state.get_data()
    day = date.fromisoformat(data["block_day"])
    schedule = ScheduleRepo(db)
    rule = await schedule.resolve_working_day(day)
    taken = await BookingsRepo(db).active_slot_starts(day)
    if not rule.is_working or rule.start is None or rule.end is None:
        await callback.answer("День больше не рабочий.", show_alert=True)
        return
    slots = {slot_key(s): (s, e) for s, e in generate_slots(rule.start, rule.end)}
    if key not in slots:
        await callback.answer("Слота нет в сетке дня.", show_alert=True)
        return
    if key in taken:
        await callback.answer("Слот занят записью — блокировать нельзя.", show_alert=True)
        return
    slot_start, slot_end = slots[key]
    async with db.transaction():
        await schedule.block_slot(day, slot_start, slot_end)
    if isinstance(callback.message, Message):
        await callback.message.answer(f"✅ Слот {fmt_date(day)} {slot_start:%H:%M}–{slot_end:%H:%M} заблокирован.")
        await _send_day_grid(callback.message, db, day)
    await callback.answer()


@router.callback_query(F.data == "sch:blkrm")
async def cb_blk_rm(callback: CallbackQuery, db: Database) -> None:
    rows = await ScheduleRepo(db).dates_with_blocks(today_msk())
    if not rows:
        await callback.answer("Заблокированных слотов нет.", show_alert=True)
        return
    builder = InlineKeyboardBuilder()
    for row in rows:
        builder.button(
            text=f"🔓 {fmt_date(date.fromisoformat(row['date']))} ({row['cnt']})",
            callback_data=f"sch:blkrmd:{row['date']}",
        )
    builder.button(text="⬅️ Блокировки", callback_data="sch:blk")
    builder.adjust(1)
    await _edit(callback, "Заблокированные слоты по датам:", builder.as_markup())


@router.callback_query(F.data.startswith("sch:blkrmd:"))
async def cb_blk_rm_date(callback: CallbackQuery, db: Database) -> None:
    day = date.fromisoformat(callback.data.split(":")[2])
    rows = await ScheduleRepo(db).blocked_slots(day)
    builder = InlineKeyboardBuilder()
    for row in rows:
        builder.button(
            text=f"🔓 {row['slot_start']}–{row['slot_end']}",
            callback_data=f"sch:unblk:{day.isoformat()}:{row['slot_start']}",
        )
    builder.button(text="⬅️ К датам", callback_data="sch:blkrm")
    builder.adjust(2, 1)
    await _edit(
        callback,
        f"Блокировки на {fmt_date(day)}. Что разблокировать?",
        builder.as_markup(),
    )


@router.callback_query(F.data.startswith("sch:unblk:"))
async def cb_unblk(callback: CallbackQuery, db: Database) -> None:
    parts = callback.data.split(":")
    day = date.fromisoformat(parts[2])
    hh, mm = parts[3].split(":")
    async with db.transaction():
        removed = await ScheduleRepo(db).unblock_slot(day, time(int(hh), int(mm)))
    if isinstance(callback.message, Message):
        if removed:
            await callback.message.answer(f"✅ Слот {fmt_date(day)} {parts[3]} разблокирован.")
        else:
            await callback.message.answer("Блокировка не найдена.")
        rows = await ScheduleRepo(db).blocked_slots(day)
        if rows:
            builder = InlineKeyboardBuilder()
            for row in rows:
                builder.button(
                    text=f"🔓 {row['slot_start']}–{row['slot_end']}",
                    callback_data=f"sch:unblk:{day.isoformat()}:{row['slot_start']}",
                )
            builder.button(text="⬅️ К датам", callback_data="sch:blkrm")
            builder.adjust(2, 1)
            await callback.message.answer(
                f"Остальные блокировки на {fmt_date(day)}:", reply_markup=builder.as_markup()
            )
    await callback.answer()


# ── отмена рабочего дня (UC-8, A.2.6–A.2.8) ──

@router.callback_query(F.data == "sch:cday")
async def cb_cday(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(ScheduleStates.waiting_cancel_date)
    if isinstance(callback.message, Message):
        await callback.message.answer(
            "Какой рабочий день отменяем? День станет нерабочим (исключение).\n"
            f"{DATE_HINT}\nОтмена — /menu"
        )
    await callback.answer()


@router.message(ScheduleStates.waiting_cancel_date)
async def cday_date_input(
    message: Message,
    db: Database,
    state: FSMContext,
    booking_service: BookingService,
) -> None:
    day = parse_user_date(message.text or "", today_msk())
    if day is None:
        await message.answer(DATE_HINT)
        return
    count = await booking_service.count_active_bookings(day)
    if count == 0:
        await booking_service.cancel_working_day(day)
        await state.clear()
        await message.answer(
            f"✅ Рабочий день {fmt_date(day)} отменён. Активных записей на дату не было."
        )
        return
    await state.update_data(cancel_day=day.isoformat())
    await state.set_state(ScheduleStates.waiting_cancel_confirm)
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Да", callback_data="sch:cdayes")
    builder.button(text="❌ Нет", callback_data="sch:cdayno")
    builder.adjust(2)
    # Дословный текст предупреждения из ТЗ (п. 4.2)
    await message.answer(
        "Вы уверены, что хотите удалить рабочий день, на который есть запись? "
        "Если да, свяжитесь с клиентом\n"
        f"Записей на {fmt_date(day)}: {count}.",
        reply_markup=builder.as_markup(),
    )


@router.callback_query(F.data == "sch:cdayes", ScheduleStates.waiting_cancel_confirm)
async def cb_cday_yes(
    callback: CallbackQuery,
    state: FSMContext,
    booking_service: BookingService,
) -> None:
    data = await state.get_data()
    day = date.fromisoformat(data["cancel_day"])
    views = await booking_service.cancel_working_day(day)
    await state.clear()
    if isinstance(callback.message, Message):
        await callback.message.answer(
            f"✅ Рабочий день {fmt_date(day)} отменён. Отменено записей: {len(views)}. "
            "Клиентам отправлены уведомления, слоты освобождены."
        )
    await callback.answer()


@router.callback_query(F.data == "sch:cdayno", ScheduleStates.waiting_cancel_confirm)
async def cb_cday_no(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await _edit(callback, "Действие отменено: рабочий день не менялся, записи на месте.")
