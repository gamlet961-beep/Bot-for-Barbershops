"""«Связаться с барбером» — режим «Клиент» (ТЗ, п. 5.8; A.5.12).

Кнопка-ссылка на личный чат (t.me/…). Пересылка сообщений через бота
не реализуется (п. 5.8 ТЗ).
"""

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.keyboards import ClientMenu

router = Router(name="client_contact")


@router.message(F.text == ClientMenu.CONTACT, StateFilter(None))
async def contact(message: Message, state: FSMContext, config) -> None:
    await state.clear()
    builder = InlineKeyboardBuilder()
    builder.button(text="💬 Написать барберу", url=config.BARBER_CONTACT_URL)
    builder.adjust(1)
    await message.answer(
        "Нажмите кнопку ниже — откроется личный чат с барбером.",
        reply_markup=builder.as_markup(),
    )
