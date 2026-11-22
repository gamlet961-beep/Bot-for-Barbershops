"""Управление услугами — режим «Админ» (ТЗ, п. 4.3; A.3.1–A.3.6, UC-5).

Добавление и редактирование услуг (название, цена, описание, категория),
скрытие из клиентского меню (soft delete) без затрагивания записей.
"""

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.db.database import Database
from bot.db.repositories.services import ServicesRepo
from bot.filters.is_admin import IsAdmin
from bot.keyboards import AdminMenu
from bot.states.admin_states import ServiceStates
from bot.utils import formatting as fmt

router = Router(name="admin_services")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())

PRICE_HINT = "Введите цену целым числом в рублях, например: 1500"
NAME_HINT = "Введите название услуги (до 100 символов), например: Мужская стрижка"
DESC_HINT = "Введите описание услуги (до 500 символов) или нажмите «Пропустить»"


async def _edit(callback: CallbackQuery, text: str, keyboard: InlineKeyboardMarkup | None = None) -> None:
    if isinstance(callback.message, Message):
        await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer()


def _services_keyboard(db: Database, grouped: dict[int, list], categories: list) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for category in categories:
        for service in grouped.get(category["id"], []):
            mark = "" if service["is_active"] else " (скрыта)"
            builder.button(
                text=f"{service['name']} — {service['price']} ₽{mark}",
                callback_data=f"svc:one:{service['id']}",
            )
    builder.button(text="➕ Добавить услугу", callback_data="svc:add")
    builder.button(text="⬅️ Меню админа", callback_data="svc:menu")
    builder.adjust(1)
    return builder.as_markup()


async def _send_services(message: Message, db: Database) -> None:
    repo = ServicesRepo(db)
    grouped = await repo.active_by_category()
    categories = await repo.categories()
    lines = ["<b>Услуги по категориям:</b>"]
    for category in categories:
        services = grouped.get(category["id"], [])
        if services:
            lines.append(f"\n{fmt.esc(category['name'])}:")
            for service in services:
                lines.append(f"• {fmt.esc(service['name'])} — {service['price']} ₽")
        else:
            lines.append(f"\n{fmt.esc(category['name'])}: — нет активных услуг —")
    hidden = await repo.list(only_active=False)
    hidden_count = sum(1 for s in hidden if not s["is_active"])
    if hidden_count:
        lines.append(f"\nСкрытых услуг: {hidden_count} (нажмите на них в списке ниже).")
    await message.answer(
        "\n".join(lines),
        reply_markup=_services_keyboard(db, grouped, categories),
    )


@router.message(F.text == AdminMenu.SERVICES)
async def open_services(message: Message, db: Database, state: FSMContext) -> None:
    await state.clear()
    await _send_services(message, db)


# ── карточка услуги ──

def _service_card(service) -> str:
    status = "активна, видна клиентам" if service["is_active"] else "скрыта из клиентского меню"
    description = service["description"] or "—"
    return (
        f"<b>{fmt.esc(service['name'])}</b>\n"
        f"Категория: {fmt.esc(service['category_name'])}\n"
        f"Цена: {service['price']} ₽\n"
        f"Описание: {fmt.esc(description)}\n"
        f"Статус: {status}."
    )


def _service_keyboard(service) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    sid = service["id"]
    builder.button(text="✏️ Название", callback_data=f"svc:setname:{sid}")
    builder.button(text="💰 Цена", callback_data=f"svc:setprice:{sid}")
    builder.button(text="📝 Описание", callback_data=f"svc:setdesc:{sid}")
    builder.button(text="📂 Категория", callback_data=f"svc:catpick:{sid}")
    if service["is_active"]:
        builder.button(text="🙈 Скрыть из меню", callback_data=f"svc:toggle:{sid}")
    else:
        builder.button(text="👁 Показать в меню", callback_data=f"svc:toggle:{sid}")
    builder.button(text="⬅️ Услуги", callback_data="svc:menu")
    builder.adjust(2, 2, 1, 1)
    return builder.as_markup()


@router.callback_query(F.data == "svc:menu")
async def cb_menu(callback: CallbackQuery, db: Database, state: FSMContext) -> None:
    await state.clear()
    if isinstance(callback.message, Message):
        await _send_services(callback.message, db)
    await callback.answer()


@router.callback_query(F.data.startswith("svc:one:"))
async def cb_service(callback: CallbackQuery, db: Database) -> None:
    service_id = int(callback.data.split(":")[2])
    service = await ServicesRepo(db).get(service_id)
    if service is None:
        await callback.answer("Услуга не найдена.", show_alert=True)
        return
    await _edit(callback, _service_card(service), _service_keyboard(service))


# ── добавление услуги ──

@router.callback_query(F.data == "svc:add")
async def cb_add(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(ServiceStates.waiting_name)
    if isinstance(callback.message, Message):
        await callback.message.answer(f"{NAME_HINT}\nОтмена — /menu")
    await callback.answer()


@router.message(ServiceStates.waiting_name)
async def add_name(message: Message, state: FSMContext) -> None:
    name = (message.text or "").strip()
    if not name or len(name) > 100:
        await message.answer(NAME_HINT)
        return
    await state.update_data(new_name=name)
    await state.set_state(ServiceStates.waiting_price)
    await message.answer(PRICE_HINT)


@router.message(ServiceStates.waiting_price)
async def add_price(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip().replace(" ", "").replace("₽", "").replace("руб.", "")
    if not text.isdigit() or int(text) <= 0:
        await message.answer(PRICE_HINT)
        return
    await state.update_data(new_price=int(text))
    await state.set_state(ServiceStates.waiting_description)
    builder = InlineKeyboardBuilder()
    builder.button(text="Пропустить", callback_data="svc:descskip")
    await message.answer(DESC_HINT, reply_markup=builder.as_markup())


@router.callback_query(F.data == "svc:descskip", ServiceStates.waiting_description)
async def cb_desc_skip(callback: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(new_description=None)
    await _ask_category(callback, state)


@router.message(ServiceStates.waiting_description)
async def add_description(message: Message, state: FSMContext) -> None:
    description = (message.text or "").strip()
    if len(description) > 500:
        await message.answer(DESC_HINT)
        return
    await state.update_data(new_description=description or None)
    await _ask_category_text(message, state)


async def _ask_category(callback: CallbackQuery, state: FSMContext) -> None:
    if isinstance(callback.message, Message):
        await _ask_category_text(callback.message, state)
    await callback.answer()


async def _ask_category_text(message: Message, state: FSMContext) -> None:
    await state.set_state(ServiceStates.waiting_category)
    await message.answer("Выберите категорию:", reply_markup=await _categories_keyboard())


async def _categories_keyboard() -> InlineKeyboardMarkup:
    # категории фиксированы справочником (допущение № 2 плана)
    builder = InlineKeyboardBuilder()
    builder.button(text="Стрижка", callback_data="svc:cat:1")
    builder.button(text="Борода и усы", callback_data="svc:cat:2")
    builder.adjust(2)
    return builder.as_markup()


@router.callback_query(F.data.startswith("svc:cat:"), ServiceStates.waiting_category)
async def cb_category(callback: CallbackQuery, db: Database, state: FSMContext) -> None:
    category_id = int(callback.data.split(":")[2])
    data = await state.get_data()
    service_id = await ServicesRepo(db).create(
        category_id=category_id,
        name=data["new_name"],
        description=data.get("new_description"),
        price=data["new_price"],
    )
    await state.clear()
    service = await ServicesRepo(db).get(service_id)
    if isinstance(callback.message, Message):
        await callback.message.answer("✅ Услуга добавлена:\n" + _service_card(service))
        await _send_services(callback.message, db)
    await callback.answer()


# ── редактирование ──

@router.callback_query(F.data.startswith("svc:setname:"))
async def cb_set_name(callback: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(edit_id=int(callback.data.split(":")[2]))
    await state.set_state(ServiceStates.waiting_edit_name)
    if isinstance(callback.message, Message):
        await callback.message.answer(f"{NAME_HINT}\nОтмена — /menu")
    await callback.answer()


@router.message(ServiceStates.waiting_edit_name)
async def edit_name(message: Message, db: Database, state: FSMContext) -> None:
    name = (message.text or "").strip()
    if not name or len(name) > 100:
        await message.answer(NAME_HINT)
        return
    data = await state.get_data()
    await ServicesRepo(db).update(data["edit_id"], name=name)
    await state.clear()
    service = await ServicesRepo(db).get(data["edit_id"])
    await message.answer("✅ Название обновлено:\n" + _service_card(service),
                         reply_markup=_service_keyboard(service))


@router.callback_query(F.data.startswith("svc:setprice:"))
async def cb_set_price(callback: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(edit_id=int(callback.data.split(":")[2]))
    await state.set_state(ServiceStates.waiting_edit_price)
    if isinstance(callback.message, Message):
        await callback.message.answer(f"{PRICE_HINT}\nОтмена — /menu")
    await callback.answer()


@router.message(ServiceStates.waiting_edit_price)
async def edit_price(message: Message, db: Database, state: FSMContext) -> None:
    text = (message.text or "").strip().replace(" ", "").replace("₽", "").replace("руб.", "")
    if not text.isdigit() or int(text) <= 0:
        await message.answer(PRICE_HINT)
        return
    data = await state.get_data()
    await ServicesRepo(db).update(data["edit_id"], price=int(text))
    await state.clear()
    service = await ServicesRepo(db).get(data["edit_id"])
    await message.answer("✅ Цена обновлена:\n" + _service_card(service),
                         reply_markup=_service_keyboard(service))


@router.callback_query(F.data.startswith("svc:setdesc:"))
async def cb_set_desc(callback: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(edit_id=int(callback.data.split(":")[2]))
    await state.set_state(ServiceStates.waiting_edit_description)
    builder = InlineKeyboardBuilder()
    builder.button(text="Оставить без описания", callback_data="svc:descnull")
    if isinstance(callback.message, Message):
        await callback.message.answer(
            f"{DESC_HINT}\nОтмена — /menu", reply_markup=builder.as_markup()
        )
    await callback.answer()


@router.callback_query(F.data == "svc:descnull", ServiceStates.waiting_edit_description)
async def cb_desc_null(callback: CallbackQuery, db: Database, state: FSMContext) -> None:
    data = await state.get_data()
    await ServicesRepo(db).update(data["edit_id"], description=None)
    await state.clear()
    service = await ServicesRepo(db).get(data["edit_id"])
    if isinstance(callback.message, Message):
        await callback.message.answer(
            "✅ Описание очищено:\n" + _service_card(service),
            reply_markup=_service_keyboard(service),
        )
    await callback.answer()


@router.message(ServiceStates.waiting_edit_description)
async def edit_description(message: Message, db: Database, state: FSMContext) -> None:
    description = (message.text or "").strip()
    if len(description) > 500:
        await message.answer(DESC_HINT)
        return
    data = await state.get_data()
    await ServicesRepo(db).update(data["edit_id"], description=description or None)
    await state.clear()
    service = await ServicesRepo(db).get(data["edit_id"])
    await message.answer("✅ Описание обновлено:\n" + _service_card(service),
                         reply_markup=_service_keyboard(service))


@router.callback_query(F.data.startswith("svc:catpick:"))
async def cb_cat_pick(callback: CallbackQuery, state: FSMContext) -> None:
    service_id = int(callback.data.split(":")[2])
    await state.update_data(edit_id=service_id)
    builder = InlineKeyboardBuilder()
    builder.button(text="Стрижка", callback_data=f"svc:catset:{service_id}:1")
    builder.button(text="Борода и усы", callback_data=f"svc:catset:{service_id}:2")
    builder.adjust(2)
    await _edit(callback, "Выберите новую категорию:", builder.as_markup())


@router.callback_query(F.data.startswith("svc:catset:"))
async def cb_cat_set(callback: CallbackQuery, db: Database, state: FSMContext) -> None:
    parts = callback.data.split(":")
    service_id, category_id = int(parts[2]), int(parts[3])
    await ServicesRepo(db).update(service_id, category_id=category_id)
    await state.clear()
    service = await ServicesRepo(db).get(service_id)
    if isinstance(callback.message, Message):
        await callback.message.answer(
            "✅ Категория обновлена:\n" + _service_card(service),
            reply_markup=_service_keyboard(service),
        )
    await callback.answer()


# ── скрытие / показ (A.3.3–A.3.5) ──

@router.callback_query(F.data.startswith("svc:toggle:"))
async def cb_toggle(callback: CallbackQuery, db: Database) -> None:
    service_id = int(callback.data.split(":")[2])
    repo = ServicesRepo(db)
    service = await repo.get(service_id)
    if service is None:
        await callback.answer("Услуга не найдена.", show_alert=True)
        return
    new_state = not service["is_active"]
    await repo.set_active(service_id, new_state)
    service = await repo.get(service_id)
    if new_state:
        note = "Услуга снова видна клиентам в меню выбора."
    else:
        note = (
            "Услуга скрыта из клиентского меню. Существующие и будущие записи "
            "с ней остаются активными и видимыми в разделе «Записи»."
        )
    if isinstance(callback.message, Message):
        await callback.message.answer(
            "✅ " + _service_card(service) + f"\n\n{note}",
            reply_markup=_service_keyboard(service),
        )
    await callback.answer()
