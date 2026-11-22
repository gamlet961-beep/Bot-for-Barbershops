"""Старт и главное меню режима «Клиент» (ТЗ, п. 5.1).

Все 5 пунктов меню подключены своими модулями: Услуга, Дата и время,
К записи (фаза 5), Мои записи, Связаться с барбером (фаза 8).
"""

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from bot.db.database import Database
from bot.db.repositories.admins import AdminsRepo
from bot.keyboards.admin import admin_menu
from bot.keyboards.client import main_menu

router = Router(name="client_start")

WELCOME = (
    "Привет! Это бот записи к барберу.\n\n"
    "Выберите услугу, дату и время — и запишитесь за пару шагов. "
    "Ваши записи и отмена — в разделе «Мои записи»."
)


@router.message(CommandStart())
async def cmd_start(message: Message, db: Database, state: FSMContext) -> None:
    await state.clear()
    user = message.from_user
    assert user is not None
    if await AdminsRepo(db).is_admin(user.id):
        is_primary = await AdminsRepo(db).is_primary(user.id)
        await message.answer(
            "Вы вошли как администратор. Открыто меню админа.",
            reply_markup=admin_menu(is_primary),
        )
        return
    await message.answer(WELCOME, reply_markup=main_menu())


@router.message(Command("menu"))
async def cmd_menu(message: Message, db: Database, state: FSMContext) -> None:
    await cmd_start(message, db, state)
