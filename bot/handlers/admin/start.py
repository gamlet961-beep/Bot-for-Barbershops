"""Старт и главное меню режима «Админ» (ТЗ, разделы 3–4).

Роутер закрыт фильтром IsAdmin: посторонние не попадают в админские
обработчики ни при каких действиях (п. 3.2 ТЗ, A.1.1 акта).
"""

from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from bot.db.database import Database
from bot.db.repositories.admins import AdminsRepo
from bot.filters.is_admin import IsAdmin
from bot.keyboards import AdminMenu
from bot.keyboards.admin import admin_menu

router = Router(name="admin_start")
router.message.filter(IsAdmin())

IN_DEVELOPMENT: dict[str, str] = {}


@router.message(CommandStart())
async def admin_start(message: Message, db: Database, state: FSMContext) -> None:
    await state.clear()
    user = message.from_user
    assert user is not None
    is_primary = await AdminsRepo(db).is_primary(user.id)
    await message.answer(
        "Меню администратора. Разделы: расписание, услуги, записи.",
        reply_markup=admin_menu(is_primary),
    )


@router.message(F.text.in_(set(IN_DEVELOPMENT)))
async def menu_stub(message: Message, state: FSMContext) -> None:
    await state.clear()
    text = message.text or ""
    await message.answer(IN_DEVELOPMENT.get(text, "Раздел в разработке."))
