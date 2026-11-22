"""Ручное создание записи — режим «Админ» (ТЗ, п. 4.5; A.4.5–A.4.7, UC-6).

Выбор услуг и слота аналогичен клиентскому сценарию, затем ввод имени и
телефона клиента. Мгновенные уведомления не отправляются никому;
напоминание админу за 1 час планируется штатно (сервис записи).
Если телефон совпадает с зарегистрированным клиентом — запись привязывается
к нему, и напоминания клиенту 24/2 ч планируются по п. 7.1 ТЗ.
"""

from datetime import date, time

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.config import Settings
from bot.db.database import Database
from bot.db.repositories.services import ServicesRepo
from bot.db.repositories.users import UsersRepo
from bot.filters.is_admin import IsAdmin
from bot.services.booking import BookingService, SlotTakenError
from bot.services.scheduling import (
    NO_SLOTS_TEXT,
    SLOT_TAKEN_TEXT,
    within_horizon,
)
from bot.states.admin_states import ManualBookingStates
from bot.utils import formatting as fmt
from bot.utils.dates import (
    WEEKDAY_SHORT,
    fmt_date_weekday,
    parse_user_date,
    today_msk,
    week_days,
)
from bot.utils.phone import normalize_phone

router = Router(name="admin_manual")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


async def _day_slots(db: Database, day: date) -> list[tuple[time, time]]:
    from bot.handlers.client.datetime import _day_free_slots

    return await _day_free_slots(db, day)


async def _send_summary(message: Message, db: Database, state: FSMContext) -> None:
    data = await state.get_data()
    service_ids: list[int] = data.get("mnl_service_ids", [])
    repo = ServicesRepo(db)
    names = []
    total = 0
    for service_id in service_ids:
        service = await repo.get(service_id)
        if service:
            names.append(f"{service['name']} ({service['price']} ₽)")
            total += service["price"]
    day = date.fromisoformat(data["day"])
    lines = [
        "<b>Новая запись (создание администратором)</b>",
        "Услуги: " + ("; ".join(names) if names else "—"),
        f"Дата: {fmt_date_weekday(day)}",
        f"Время: {data['slot_start']}–{data['slot_end']}",
        f"Сумма: {total} ₽",
        f"Клиент: {fmt.esc(data.get('mnl_name', '—'))}, {fmt.esc(data.get('mnl_phone', '—'))}",
        "\nМгновенные уведомления отправляться не будут (п. 4.5 ТЗ).",
    ]
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Создать запись", callback_data="mnl:submit")
    builder.button(text="⬅️ Отмена", callback_data="mnl:cancel")
    builder.adjust(1)
    await message.answer("\n".join(lines), reply_markup=builder.as_markup())


@router.callback_query(F.data == "mnl:start")
async def cb_start(callback: CallbackQuery, db: Database, state: FSMContext) -> None:
    await state.set_state(ManualBookingStates.choosing_services)
    await state.update_data(mnl_service_ids=[])
    repo = ServicesRepo(db)
    grouped = await repo.active_by_category()
    builder = InlineKeyboardBuilder()
    for category in await repo.categories():
        for service in grouped.get(category["id"], []):
            builder.button(
                text=f"{service['name']} — {service['price']} ₽",
                callback_data=f"mnl:tgl:{service['id']}",
            )
    builder.button(text="Далее: дата →", callback_data="mnl:next")
    builder.adjust(1)
    if isinstance(callback.message, Message):
        await callback.message.answer(
            "Ручная запись. Выберите услуги (можно несколько):",
            reply_markup=builder.as_markup(),
        )
    await callback.answer()


@router.callback_query(F.data.startswith("mnl:tgl:"))
async def cb_toggle(callback: CallbackQuery, db: Database, state: FSMContext) -> None:
    service_id = int(callback.data.split(":")[2])
    service = await ServicesRepo(db).get(service_id)
    if service is None or not service["is_active"]:
        await callback.answer("Услуга недоступна.", show_alert=True)
        return
    data = await state.get_data()
    selected: list[int] = list(data.get("mnl_service_ids", []))
    if service_id in selected:
        selected.remove(service_id)
        await callback.answer("Убрано.")
    else:
        selected.append(service_id)
        await callback.answer("Добавлено.")
    await state.update_data(mnl_service_ids=selected)
    if isinstance(callback.message, Message):
        repo = ServicesRepo(db)
        grouped = await repo.active_by_category()
        mark = {sid: True for sid in selected}
        builder = InlineKeyboardBuilder()
        for category in await repo.categories():
            for service in grouped.get(category["id"], []):
                check = "✓ " if mark.get(service["id"]) else ""
                builder.button(
                    text=f"{check}{service['name']} — {service['price']} ₽",
                    callback_data=f"mnl:tgl:{service['id']}",
                )
        builder.button(text="Далее: дата →", callback_data="mnl:next")
        builder.adjust(1)
        await callback.message.edit_text(
            "Ручная запись. Выберите услуги (можно несколько):",
            reply_markup=builder.as_markup(),
        )


@router.callback_query(F.data == "mnl:next", StateFilter(ManualBookingStates.choosing_services))
async def cb_next(callback: CallbackQuery, db: Database, state: FSMContext) -> None:
    data = await state.get_data()
    if not data.get("mnl_service_ids"):
        await callback.answer("Выберите хотя бы одну услугу.", show_alert=True)
        return
    await state.set_state(ManualBookingStates.choosing_datetime)
    await _send_week(callback, db)
    await callback.answer()


async def _send_week(callback: CallbackQuery, db: Database) -> None:
    today = today_msk()
    builder = InlineKeyboardBuilder()
    for day in week_days(today):
        if day < today:
            continue
        slots = await _day_slots(db, day)
        if slots:
            builder.button(
                text=f"{WEEKDAY_SHORT[day.weekday()]} {day.strftime('%d.%m')} ({len(slots)})",
                callback_data=f"mnl:day:{day.isoformat()}",
            )
    builder.button(text="🗓 Другая дата", callback_data="mnl:other")
    builder.adjust(3, 1)
    if isinstance(callback.message, Message):
        await callback.message.answer(
            "Дата записи — текущая неделя (свободные слоты):",
            reply_markup=builder.as_markup(),
        )


@router.callback_query(F.data.startswith("mnl:day:"))
async def cb_day(callback: CallbackQuery, db: Database, state: FSMContext) -> None:
    day = date.fromisoformat(callback.data.split(":")[2])
    await state.update_data(day=day.isoformat())
    await _send_slots(callback, db, day)
    await callback.answer()


async def _send_slots(callback: CallbackQuery, db: Database, day: date) -> None:
    slots = await _day_slots(db, day)
    if not slots:
        if isinstance(callback.message, Message):
            await callback.message.answer(f"{fmt_date_weekday(day)}: {NO_SLOTS_TEXT}")
        return
    builder = InlineKeyboardBuilder()
    for slot_start, slot_end in slots:
        builder.button(
            text=f"{slot_start:%H:%M}–{slot_end:%H:%M}",
            callback_data=f"mnl:slot:{slot_start.strftime('%H%M')}",
        )
    builder.button(text="⬅️ К неделям", callback_data="mnl:next")
    builder.adjust(3, 1)
    if isinstance(callback.message, Message):
        await callback.message.answer(
            f"<b>{fmt_date_weekday(day)}</b> — свободные слоты:",
            reply_markup=builder.as_markup(),
        )


@router.callback_query(F.data == "mnl:other")
async def cb_other(callback: CallbackQuery) -> None:
    if isinstance(callback.message, Message):
        await callback.message.answer("Введите дату записи (ДД.ММ или ДД.ММ.ГГГГ):")
    await callback.answer()


@router.message(ManualBookingStates.choosing_datetime, F.text)
async def other_date_input(
    message: Message, db: Database, state: FSMContext, config: Settings
) -> None:
    day = parse_user_date(message.text or "", today_msk())
    if day is None:
        await message.answer("Не понял дату. Введите в формате ДД.ММ или ДД.ММ.ГГГГ.")
        return
    if not within_horizon(day, today_msk(), config.BOOKING_HORIZON_DAYS):
        await message.answer(
            f"Дата вне горизонта записи (до {config.BOOKING_HORIZON_DAYS} дней вперёд)."
        )
        return
    await state.update_data(day=day.isoformat())
    slots = await _day_slots(db, day)
    if not slots:
        await message.answer(f"{fmt_date_weekday(day)}: {NO_SLOTS_TEXT}")
        return
    builder = InlineKeyboardBuilder()
    for slot_start, slot_end in slots:
        builder.button(
            text=f"{slot_start:%H:%M}–{slot_end:%H:%M}",
            callback_data=f"mnl:slot:{slot_start.strftime('%H%M')}",
        )
    builder.adjust(3)
    await message.answer(
        f"<b>{fmt_date_weekday(day)}</b> — свободные слоты:", reply_markup=builder.as_markup()
    )


@router.callback_query(F.data.startswith("mnl:slot:"))
async def cb_slot(callback: CallbackQuery, db: Database, state: FSMContext) -> None:
    data = await state.get_data()
    day = date.fromisoformat(data["day"])
    hhmm = callback.data.split(":")[2]
    target = time(int(hhmm[:2]), int(hhmm[2:]))
    slots = await _day_slots(db, day)
    match = next(((s, e) for s, e in slots if s == target), None)
    if match is None:
        await callback.answer(NO_SLOTS_TEXT, show_alert=True)
        return
    await state.update_data(slot_start=match[0].strftime("%H:%M"), slot_end=match[1].strftime("%H:%M"))
    await state.set_state(ManualBookingStates.entering_name)
    if isinstance(callback.message, Message):
        await callback.message.answer("Имя клиента:")
    await callback.answer()


@router.message(ManualBookingStates.entering_name, F.text)
async def input_name(message: Message, state: FSMContext) -> None:
    name = (message.text or "").strip()
    if not 1 <= len(name) <= 100:
        await message.answer("Введите имя (до 100 символов).")
        return
    await state.update_data(mnl_name=name)
    await state.set_state(ManualBookingStates.entering_phone)
    await message.answer("Телефон клиента (+7XXXXXXXXXX, 8… или 9…):")


@router.message(ManualBookingStates.entering_phone, F.text)
async def input_phone(message: Message, db: Database, state: FSMContext) -> None:
    phone = normalize_phone(message.text or "")
    if phone is None:
        await message.answer("Неверный формат телефона. Форматы: +7XXXXXXXXXX, 8XXXXXXXXXX, 9XXXXXXXXXX.")
        return
    await state.update_data(mnl_phone=phone)
    await state.set_state(ManualBookingStates.confirming)
    await _send_summary(message, db, state)


@router.callback_query(F.data == "mnl:submit", StateFilter(ManualBookingStates.confirming))
async def cb_submit(
    callback: CallbackQuery,
    db: Database,
    state: FSMContext,
    booking_service: BookingService,
) -> None:
    data = await state.get_data()
    repo = ServicesRepo(db)
    services: list[dict] = []
    for service_id in data.get("mnl_service_ids", []):
        service = await repo.get(service_id)
        if service and service["is_active"]:
            services.append({"id": service["id"], "name": service["name"], "price": service["price"]})
    if not services or not data.get("day") or not data.get("slot_start"):
        await callback.answer("Данные устарели. Начните заново.", show_alert=True)
        await state.clear()
        return
    day = date.fromisoformat(data["day"])
    slot = (time.fromisoformat(data["slot_start"]), time.fromisoformat(data["slot_end"]))
    # Снимки имени/телефона сохраняются всегда; при совпадении телефона
    # с зарегистрированным клиентом запись дополнительно привязывается к нему.
    user = await UsersRepo(db).get_by_phone(data["mnl_phone"])
    client = {
        "user_id": user["id"] if user else None,
        "tg_id": user["telegram_id"] if user else None,
        "name": data["mnl_name"],
        "phone": data["mnl_phone"],
    }
    try:
        booking_id = await booking_service.create_booking(
            service_rows=services,
            day=day,
            slot=slot,
            client=client,
            created_by="admin",
            pd_consent=False,  # ручная запись: клиент не давал согласия в боте
        )
    except SlotTakenError:
        await callback.answer(SLOT_TAKEN_TEXT, show_alert=True)
        return
    await state.clear()
    if isinstance(callback.message, Message):
        await callback.message.answer(
            f"✅ Запись #{booking_id} создана.\n"
            "Мгновенные уведомления не отправлялись. "
            "Напоминание админу за 1 час запланировано."
        )
    await callback.answer()


@router.callback_query(F.data == "mnl:cancel")
async def cb_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    if isinstance(callback.message, Message):
        from bot.handlers.admin.bookings import _bookings_menu

        await callback.message.answer(
            "Создание записи отменено.", reply_markup=_bookings_menu()
        )
    await callback.answer()
