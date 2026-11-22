"""Клавиатуры режима «Админ»."""

from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

from bot.keyboards import AdminMenu


def admin_menu(is_primary: bool) -> ReplyKeyboardMarkup:
    """Главное меню админа: 4 пункта; «Управление админами» — только основной."""
    rows = [
        [KeyboardButton(text=AdminMenu.SCHEDULE), KeyboardButton(text=AdminMenu.SERVICES)],
        [KeyboardButton(text=AdminMenu.BOOKINGS)],
    ]
    if is_primary:
        rows.append([KeyboardButton(text=AdminMenu.ADMINS)])
    return ReplyKeyboardMarkup(
        keyboard=rows,
        resize_keyboard=True,
        input_field_placeholder="Раздел администратора",
    )
