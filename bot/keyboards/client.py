"""Клавиатуры режима «Клиент»."""

from aiogram.types import InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.keyboards import ClientMenu


def main_menu() -> ReplyKeyboardMarkup:
    """Главное меню клиента: 5 пунктов из ТЗ (п. 5.1)."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=ClientMenu.SERVICE), KeyboardButton(text=ClientMenu.DATETIME)],
            [KeyboardButton(text=ClientMenu.CART)],
            [KeyboardButton(text=ClientMenu.MY_BOOKINGS), KeyboardButton(text=ClientMenu.CONTACT)],
        ],
        resize_keyboard=True,
        input_field_placeholder="Выберите действие",
    )


# ── inline-клавиатуры сценария записи ──

def categories_keyboard(categories: list, selected_ids: list[int], counts: dict[int, int]) -> InlineKeyboardMarkup:
    """Выбор категории услуг; счётчик выбранных в категории."""
    builder = InlineKeyboardBuilder()
    for category in categories:
        mark = f" (+{counts.get(category['id'], 0)})" if counts.get(category["id"]) else ""
        builder.button(
            text=f"{category['name']}{mark}",
            callback_data=f"cb:cat:{category['id']}",
        )
    builder.button(text="Дата и время →", callback_data="cb:nextdate")
    builder.adjust(1)
    return builder.as_markup()


def services_keyboard(category_name: str, services: list, selected_ids: list[int]) -> InlineKeyboardMarkup:
    """Услуги категории: toggle-кнопки с ценой; ✓ — выбрана."""
    builder = InlineKeyboardBuilder()
    for service in services:
        mark = "✓ " if service["id"] in selected_ids else ""
        builder.button(
            text=f"{mark}{service['name']} — {service['price']} ₽",
            callback_data=f"cb:tgl:{service['id']}",
        )
    builder.button(text="↩️ Убрать последнюю", callback_data="cb:rmv")
    builder.button(text="⬅️ Категории", callback_data="cb:cats")
    builder.button(text="Дата и время →", callback_data="cb:nextdate")
    builder.adjust(1)
    return builder.as_markup()


def week_keyboard(options: list[tuple]) -> InlineKeyboardMarkup:
    """options: [(date, label, free_count)] — только дни со свободными слотами."""
    builder = InlineKeyboardBuilder()
    for day, label, count in options:
        builder.button(text=f"{label} ({count})", callback_data=f"cb:day:{day.isoformat()}")
    builder.button(text="🗓 Записаться на другую дату", callback_data="cb:other")
    builder.button(text="⬅️ К услугам", callback_data="cb:backsvc")
    builder.adjust(3, 3, 1, 1)
    return builder.as_markup()


def slots_keyboard(day_label: str) -> InlineKeyboardMarkup:
    """Слоты добавляются кнопками динамически (cb:slot:HHMM)."""
    builder = InlineKeyboardBuilder()
    builder.button(text="⬅️ К календарю", callback_data="cb:week")
    return builder.as_markup()


def cart_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Оформить", callback_data="cb:order")
    builder.button(text="💇 Изменить услуги", callback_data="cb:redosvc")
    builder.button(text="🕒 Изменить время", callback_data="cb:redodt")
    builder.button(text="⬅️ Главное меню", callback_data="cb:menu")
    builder.adjust(1)
    return builder.as_markup()


def consent_keyboard(consented: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if consented:
        builder.button(text="☑️ Согласие на обработку ПД — дано", callback_data="cb:consent")
        builder.button(text="📅 Записаться", callback_data="cb:submit")
    else:
        builder.button(text="☐ Согласие на обработку персональных данных", callback_data="cb:consent")
    builder.button(text="⬅️ Главное меню", callback_data="cb:menu")
    builder.adjust(1)
    return builder.as_markup()

