"""Корзина «К записи» и оформление (ТЗ, п. 5.4–5.6; A.5.7–A.5.11, UC-1, UC-2, UC-7).

Порядок оформления: имя → телефон (валидация) → обязательный чекбокс
согласия на обработку ПД → «Записаться» (транзакционная проверка слота;
при занятости — «Слот занят…» и возврат к выбору времени, корзина сохраняется).
"""

from datetime import date, time

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.db.database import Database
from bot.db.repositories.services import ServicesRepo
from bot.db.repositories.users import UsersRepo
from bot.keyboards import ClientMenu
from bot.keyboards.client import cart_keyboard, consent_keyboard, main_menu
from bot.services.booking import BookingService, SlotTakenError
from bot.services.scheduling import SLOT_TAKEN_TEXT
from bot.states.client_states import ClientBookingStates
from bot.utils import formatting as fmt
from bot.utils.dates import fmt_date_weekday
from bot.utils.phone import PHONE_INVALID_TEXT, normalize_phone

router = Router(name="client_cart")

MENU_TEXTS = {
    ClientMenu.SERVICE, ClientMenu.DATETIME, ClientMenu.CART,
    ClientMenu.MY_BOOKINGS, ClientMenu.CONTACT,
}


async def _cart_view(db: Database, data: dict) -> tuple[str, list[dict]] | None:
    """Текст корзины и строки услуг; None — корзина не собрана."""
    service_ids: list[int] = data.get("service_ids") or []
    if not service_ids or not data.get("day") or not data.get("slot_start"):
        return None
    repo = ServicesRepo(db)
    services: list[dict] = []
    total = 0
    for service_id in service_ids:
        service = await repo.get(service_id)
        if service is None or not service["is_active"]:
            continue  # услуга могла быть скрыта после выбора — пропускаем
        services.append({"id": service["id"], "name": service["name"], "price": service["price"]})
        total += service["price"]
    if not services:
        return None
    day = date.fromisoformat(data["day"])
    lines = [
        "<b>К записи:</b>",
        "Услуги: " + ", ".join(f"{fmt.esc(s['name'])} ({s['price']} ₽)" for s in services),
        f"Дата: {fmt_date_weekday(day)}",
        f"Время: {data['slot_start']}–{data['slot_end']}",
        f"<b>Итого: {total} ₽</b>",
    ]
    return "\n".join(lines), services


async def send_cart(message: Message, db: Database, state: FSMContext) -> None:
    data = await state.get_data()
    cart = await _cart_view(db, data)
    if cart is None:
        await message.answer(
            "Корзина не собрана. Выберите услуги (кнопка «Услуга») "
            "и время (кнопка «Дата и время»)."
        )
        return
    await message.answer(cart[0], reply_markup=cart_keyboard())


@router.message(F.text == ClientMenu.CART, StateFilter(None))
async def open_cart(message: Message, db: Database, state: FSMContext) -> None:
    await send_cart(message, db, state)


@router.callback_query(F.data == "cb:menu", StateFilter(None))
async def cb_menu(callback: CallbackQuery) -> None:
    if isinstance(callback.message, Message):
        await callback.message.answer("Главное меню.", reply_markup=main_menu())
    await callback.answer()


@router.callback_query(F.data == "cb:redosvc")
async def cb_redo_services(callback: CallbackQuery, db: Database, state: FSMContext) -> None:
    """Изменить услуги: выбор заново, дата и слот сохраняются."""
    from bot.handlers.client.services import open_services

    if isinstance(callback.message, Message):
        await open_services(callback.message, db, state)
    await callback.answer()


@router.callback_query(F.data == "cb:redodt")
async def cb_redo_datetime(callback: CallbackQuery, db: Database, state: FSMContext) -> None:
    from bot.handlers.client.datetime import _send_week

    data = await state.get_data()
    if not data.get("service_ids"):
        await callback.answer("Сначала выберите услуги.", show_alert=True)
        return
    await state.set_state(ClientBookingStates.choosing_datetime)
    if isinstance(callback.message, Message):
        await _send_week(callback.message, db)
    await callback.answer()


@router.callback_query(F.data == "cb:order", StateFilter(None))
async def cb_order(callback: CallbackQuery, db: Database, state: FSMContext) -> None:
    data = await state.get_data()
    cart = await _cart_view(db, data)
    if cart is None:
        await callback.answer("Сначала выберите услуги и время.", show_alert=True)
        return
    await state.set_state(ClientBookingStates.entering_name)
    if isinstance(callback.message, Message):
        await callback.message.answer("Введите имя (как к вам обращаться):")
    await callback.answer()


@router.message(ClientBookingStates.entering_name, F.text)
async def input_name(message: Message, db: Database, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if text in MENU_TEXTS:
        await state.clear()
        await message.answer("Ввод отменён. Воспользуйтесь меню.")
        return
    if not 1 <= len(text) <= 100:
        await message.answer("Введите имя (до 100 символов).")
        return
    await state.update_data(name=text)
    await state.set_state(ClientBookingStates.entering_phone)
    await message.answer(
        "Введите номер телефона. Подойдут форматы +7XXXXXXXXXX, 8XXXXXXXXXX или 9XXXXXXXXXX."
    )


@router.message(ClientBookingStates.entering_phone, F.text)
async def input_phone(message: Message, db: Database, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if text in MENU_TEXTS:
        await state.clear()
        await message.answer("Ввод отменён. Воспользуйтесь меню.")
        return
    phone = normalize_phone(text)
    if phone is None:
        await message.answer(PHONE_INVALID_TEXT)
        return
    await state.update_data(phone=phone, consent=False)
    await state.set_state(ClientBookingStates.confirming)
    await _send_confirmation(message, db, state)


async def _send_confirmation(message: Message, db: Database, state: FSMContext) -> None:
    data = await state.get_data()
    cart = await _cart_view(db, data)
    assert cart is not None
    text = (
        cart[0]
        + f"\nИмя: {fmt.esc(data['name'])}\nТелефон: {fmt.esc(data['phone'])}\n\n"
        "Для оформления записи нужно согласие на обработку персональных данных (152-ФЗ)."
    )
    await message.answer(text, reply_markup=consent_keyboard(bool(data.get("consent"))))


@router.callback_query(F.data == "cb:consent", StateFilter(ClientBookingStates.confirming))
async def cb_consent(callback: CallbackQuery, db: Database, state: FSMContext) -> None:
    data = await state.get_data()
    await state.update_data(consent=not data.get("consent", False))
    if isinstance(callback.message, Message):
        await callback.message.delete_reply_markup()
        await _send_confirmation(callback.message, db, state)
    await callback.answer()


@router.callback_query(F.data == "cb:submit", StateFilter(ClientBookingStates.confirming))
async def cb_submit(
    callback: CallbackQuery,
    db: Database,
    state: FSMContext,
    booking_service: BookingService,
) -> None:
    """«Записаться»: транзакционная проверка слота (п. 6.2 ТЗ; UC-7)."""
    data = await state.get_data()
    if not data.get("consent"):
        await callback.answer("Сначала подтвердите согласие на обработку ПД.", show_alert=True)
        return
    cart = await _cart_view(db, data)
    if cart is None:
        await state.clear()
        await callback.answer("Корзина устарела. Начните заново.", show_alert=True)
        return
    _, services = cart
    day = date.fromisoformat(data["day"])
    slot = (
        time.fromisoformat(data["slot_start"]),
        time.fromisoformat(data["slot_end"]),
    )
    user = callback.from_user
    users = UsersRepo(db)
    user_id = await users.get_id_by_telegram(user.id)
    await users.set_contact(user_id, data["name"], data["phone"])  # A.1.5: tg_id + phone
    client = {
        "user_id": user_id,
        "tg_id": user.id,
        "name": data["name"],
        "phone": data["phone"],
    }
    try:
        await booking_service.create_booking(
            service_rows=services,
            day=day,
            slot=slot,
            client=client,
            created_by="client",
            pd_consent=True,
        )
    except SlotTakenError:
        # UC-7: слот заняли — возврат к выбору времени, корзина сохранена
        await callback.answer(SLOT_TAKEN_TEXT, show_alert=True)
        await state.set_state(ClientBookingStates.choosing_datetime)
        if isinstance(callback.message, Message):
            from bot.handlers.client.datetime import _send_slots

            await _send_slots(callback.message, db, day)
        return
    await state.clear()
    # Подтверждение клиенту и уведомление админам уже отправлены сервисом (п. 5.6, 4.6 ТЗ)
    await callback.answer("Готово! Запись оформлена.")
