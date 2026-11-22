"""Выбор услуг — режим «Клиент» (ТЗ, п. 5.2; A.5.2, A.5.3).

Категории → услуги с ценами и описаниями; toggle-мультивыбор,
кнопка «Убрать последнюю», переход к выбору даты.
"""

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.db.database import Database
from bot.db.repositories.services import ServicesRepo
from bot.keyboards import ClientMenu
from bot.keyboards.client import categories_keyboard, services_keyboard
from bot.states.client_states import ClientBookingStates
from bot.utils import formatting as fmt

router = Router(name="client_services")

MENU_TEXTS = {
    ClientMenu.SERVICE, ClientMenu.DATETIME, ClientMenu.CART,
    ClientMenu.MY_BOOKINGS, ClientMenu.CONTACT,
}


async def _send_categories(message: Message, db: Database, state: FSMContext) -> None:
    data = await state.get_data()
    selected_ids: list[int] = data.get("service_ids", [])
    repo = ServicesRepo(db)
    categories = await repo.categories()
    grouped = await repo.active_by_category()
    counts = {
        cat_id: sum(1 for s in services if s["id"] in selected_ids)
        for cat_id, services in grouped.items()
    }
    lines = ["<b>Выберите услуги</b> (можно несколько):"]
    if selected_ids:
        chosen = [s for services in grouped.values() for s in services if s["id"] in selected_ids]
        lines.append("Выбрано: " + ", ".join(f"{fmt.esc(s['name'])} ({s['price']} ₽)" for s in chosen))
    lines.append("\nКатегория услуг:")
    await message.answer(
        "\n".join(lines),
        reply_markup=categories_keyboard(categories, selected_ids, counts),
    )


@router.message(F.text == ClientMenu.SERVICE)
async def open_services(message: Message, db: Database, state: FSMContext) -> None:
    """Вход в выбор услуг: прежний выбор услуг сбрасывается."""
    await state.update_data(service_ids=[], service_names=[])
    await state.set_state(ClientBookingStates.choosing_services)
    await _send_categories(message, db, state)


@router.callback_query(F.data == "cb:cats", StateFilter(ClientBookingStates.choosing_services))
async def cb_cats(callback: CallbackQuery, db: Database, state: FSMContext) -> None:
    if isinstance(callback.message, Message):
        await _send_categories(callback.message, db, state)
    await callback.answer()


@router.callback_query(
    F.data.startswith("cb:cat:"), StateFilter(ClientBookingStates.choosing_services)
)
async def cb_category(callback: CallbackQuery, db: Database, state: FSMContext) -> None:
    category_id = int(callback.data.split(":")[2])
    repo = ServicesRepo(db)
    category = next((c for c in await repo.categories() if c["id"] == category_id), None)
    if category is None:
        await callback.answer("Категория не найдена.", show_alert=True)
        return
    await state.update_data(current_category=category_id)
    data = await state.get_data()
    selected_ids: list[int] = data.get("service_ids", [])
    services = (await repo.active_by_category()).get(category_id, [])
    lines = [f"<b>{fmt.esc(category['name'])}</b> — услуги:"]
    for service in services:
        mark = "✓ " if service["id"] in selected_ids else ""
        description = f" — {fmt.esc(service['description'])}" if service["description"] else ""
        lines.append(f"{mark}{fmt.esc(service['name'])}, {service['price']} ₽{description}")
    if not services:
        lines.append("— в этой категории пока нет активных услуг —")
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            "\n".join(lines),
            reply_markup=services_keyboard(category["name"], services, selected_ids),
        )
    await callback.answer()


@router.callback_query(
    F.data.startswith("cb:tgl:"), StateFilter(ClientBookingStates.choosing_services)
)
async def cb_toggle(callback: CallbackQuery, db: Database, state: FSMContext) -> None:
    """A.5.3: повторное нажатие — toggle (снять выбор)."""
    service_id = int(callback.data.split(":")[2])
    repo = ServicesRepo(db)
    service = await repo.get(service_id)
    if service is None or not service["is_active"]:
        await callback.answer("Услуга недоступна.", show_alert=True)
        return
    data = await state.get_data()
    selected_ids: list[int] = list(data.get("service_ids", []))
    if service_id in selected_ids:
        selected_ids.remove(service_id)  # toggle: снять выбор
        note = "убрана из выбора"
    else:
        selected_ids.append(service_id)
        note = "добавлена"
    await state.update_data(service_ids=selected_ids)
    # перерисовать экран категории
    category_id = data.get("current_category", service["category_id"])
    await _rerender_category(callback, db, state, category_id)
    await callback.answer(f"Услуга {note}.")


async def _rerender_category(
    callback: CallbackQuery, db: Database, state: FSMContext, category_id: int
) -> None:
    repo = ServicesRepo(db)
    category = next((c for c in await repo.categories() if c["id"] == category_id), None)
    if category is None:
        return
    data = await state.get_data()
    selected_ids: list[int] = data.get("service_ids", [])
    services = (await repo.active_by_category()).get(category_id, [])
    lines = [f"<b>{fmt.esc(category['name'])}</b> — услуги:"]
    for service in services:
        mark = "✓ " if service["id"] in selected_ids else ""
        description = f" — {fmt.esc(service['description'])}" if service["description"] else ""
        lines.append(f"{mark}{fmt.esc(service['name'])}, {service['price']} ₽{description}")
    if not services:
        lines.append("— в этой категории пока нет активных услуг —")
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            "\n".join(lines),
            reply_markup=services_keyboard(category["name"], services, selected_ids),
        )


@router.callback_query(F.data == "cb:rmv", StateFilter(ClientBookingStates.choosing_services))
async def cb_remove_last(callback: CallbackQuery, db: Database, state: FSMContext) -> None:
    """Кнопка «Убрать» — снимает последнюю добавленную услугу (п. 5.2 ТЗ)."""
    data = await state.get_data()
    selected_ids: list[int] = list(data.get("service_ids", []))
    if not selected_ids:
        await callback.answer("Ничего не выбрано.", show_alert=True)
        return
    removed = selected_ids.pop()
    await state.update_data(service_ids=selected_ids)
    service = await ServicesRepo(db).get(removed)
    name = service["name"] if service else str(removed)
    category_id = data.get("current_category", service["category_id"] if service else 1)
    await _rerender_category(callback, db, state, category_id)
    await callback.answer(f"Убрано: {name}")
