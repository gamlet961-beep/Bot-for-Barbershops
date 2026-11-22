"""Управление администраторами (раздел 3 ТЗ; A.1.2–A.1.4 акта).

Доступно только основному админу: добавление и удаление второго админа
по Telegram ID. Второй админ получает равные права на расписание, услуги
и записи, но не управляет админами.
"""

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.db.database import Database
from bot.db.repositories.admins import AdminsRepo
from bot.filters.is_admin import IsAdmin, IsPrimaryAdmin
from bot.states.admin_states import AdminManageStates

router = Router(name="admin_admins")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


def _admins_view(admins: list) -> str:
    lines = ["<b>Администраторы:</b>"]
    for row in admins:
        role = "основной" if row["is_primary"] else "второй"
        username = f", @{row['username']}" if row["username"] else ""
        lines.append(f"• ID <code>{row['telegram_id']}</code>{username} — {role}")
    return "\n".join(lines)


def _manage_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="➕ Добавить админа", callback_data="adm:add")
    builder.button(text="➖ Удалить админа", callback_data="adm:rm")
    builder.adjust(2)
    return builder.as_markup()


async def _show_admins(message: Message, db: Database) -> None:
    admins = await AdminsRepo(db).list_admins()
    await message.answer(
        _admins_view(admins) + "\n\nЧто сделать?", reply_markup=_manage_keyboard()
    )


@router.message(F.text == "Управление админами", StateFilter(None))
async def open_admins(message: Message, db: Database) -> None:
    user = message.from_user
    assert user is not None
    if not await AdminsRepo(db).is_primary(user.id):
        # A.1.4: второй админ не управляет админами
        await message.answer(
            "Управление админами доступно только основному администратору."
        )
        return
    await _show_admins(message, db)


@router.callback_query(F.data == "adm:cancel", StateFilter(None))
async def cancel_admins(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    if isinstance(callback.message, Message):
        await callback.message.answer("Управление админами закрыто.")
    await callback.answer()


@router.callback_query(F.data == "adm:add", IsPrimaryAdmin())
async def start_add(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminManageStates.waiting_add_id)
    await state.update_data(dialog="add")
    if isinstance(callback.message, Message):
        await callback.message.answer(
            "Пришлите Telegram ID нового администратора (целое число).\n"
            "Отмена — /menu"
        )
    await callback.answer()


@router.message(AdminManageStates.waiting_add_id)
async def do_add(message: Message, db: Database, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if not text.lstrip("-").isdigit():
        await message.answer("ID должен быть целым числом. Попробуйте ещё раз.")
        return
    new_id = int(text)
    user = message.from_user
    assert user is not None
    repo = AdminsRepo(db)
    if new_id == user.id:
        await message.answer("Вы уже администратор.")
        return
    if not await repo.add_admin(new_id, added_by=user.id):
        await message.answer("Этот пользователь уже является администратором.")
        return
    await message.answer(f"✅ Второй администратор добавлен: <code>{new_id}</code>.")
    await state.clear()
    await _show_admins(message, db)


@router.callback_query(F.data == "adm:rm", IsPrimaryAdmin())
async def start_remove(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminManageStates.waiting_remove_id)
    await state.update_data(dialog="remove")
    if isinstance(callback.message, Message):
        await callback.message.answer(
            "Пришлите Telegram ID администратора, которого нужно удалить "
            "(основного удалить нельзя).\nОтмена — /menu"
        )
    await callback.answer()


@router.message(AdminManageStates.waiting_remove_id)
async def do_remove(message: Message, db: Database, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if not text.lstrip("-").isdigit():
        await message.answer("ID должен быть целым числом. Попробуйте ещё раз.")
        return
    target_id = int(text)
    repo = AdminsRepo(db)
    if not await repo.is_admin(target_id):
        await message.answer("Пользователь с таким ID не является администратором.")
        return
    if await repo.is_primary(target_id):
        await message.answer("Основного администратора удалить нельзя.")
        return
    if not await repo.remove_admin(target_id):
        await message.answer("Не удалось удалить администратора.")
        return
    await message.answer(f"✅ Администратор удалён: <code>{target_id}</code>.")
    await state.clear()
    await _show_admins(message, db)
