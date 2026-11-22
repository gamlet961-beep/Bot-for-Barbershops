"""«Мои записи» — режим «Клиент» (ТЗ, п. 5.7; A.6.1–A.6.6, UC-3).

Активные и прошедшие записи за последние 2 месяца; отменённые скрыты;
предстоящие — по возрастанию, прошедшие — по убыванию. Кнопка «Отменить
запись» доступна при остатке ≥ 4 часов; при меньшем — сообщение
«Для отмены свяжитесь с барбером» + кнопка связи.
"""

from datetime import date, time

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.db.database import Database
from bot.db.repositories.bookings import BookingsRepo
from bot.db.repositories.users import UsersRepo
from bot.keyboards import ClientMenu
from bot.services.booking import BookingService
from bot.services.scheduling import (
    CANCEL_TOO_LATE_TEXT,
    can_cancel,
    my_bookings_window_start,
    split_for_display,
)
from bot.utils import formatting as fmt
from bot.utils.dates import combine_msk, fmt_date_weekday, now_msk, parse_hhmm, today_msk

router = Router(name="client_mybookings")


def _contact_keyboard(url: str):
    builder = InlineKeyboardBuilder()
    builder.button(text="💬 Связаться с барбером", url=url)
    builder.adjust(1)
    return builder.as_markup()


async def _render_bookings(message: Message, db: Database, contact_url: str) -> None:
    user = message.from_user
    assert user is not None
    user_id = await UsersRepo(db).get_id_by_telegram(user.id)
    if user_id is None:
        await message.answer("У вас пока нет записей.")
        return
    window_start = my_bookings_window_start(today_msk())
    rows = await BookingsRepo(db).active_for_user(user_id, window_start)
    if not rows:
        await message.answer("У вас нет записей за последние 2 месяца.")
        return
    upcoming, past = split_for_display(rows, today_msk())
    now = now_msk()
    cancellable: list[dict] = []
    too_late = False

    lines: list[str] = []
    if upcoming:
        lines.append("<b>Предстоящие:</b>")
        for row in upcoming:
            view = await BookingsRepo(db).get_view(row["id"])
            if view is None:
                continue
            lines.append(
                f"• {fmt.esc(view['date_weekday'])}, {fmt.esc(view['slot'])} — "
                f"{fmt.esc(view['services_line'])} ({view['total']} ₽)"
            )
            visit_start = combine_msk(
                date.fromisoformat(view["date"]), parse_hhmm(view["slot_start"])
            )
            if can_cancel(visit_start, now):
                cancellable.append(view)
            else:
                too_late = True
    if past:
        lines.append("<b>Прошедшие:</b>")
        for row in past:
            view = await BookingsRepo(db).get_view(row["id"])
            if view is None:
                continue
            lines.append(
                f"• {fmt.esc(view['date_weekday'])}, {fmt.esc(view['slot'])} — "
                f"{fmt.esc(view['services_line'])} ({view['total']} ₽)"
            )
    if too_late:
        lines.append(f"\n{CANCEL_TOO_LATE_TEXT}")

    builder = InlineKeyboardBuilder()
    for view in cancellable:
        builder.button(
            text=f"Отменить {view['date_ru']} {view['slot_start']}",
            callback_data=f"my:cancel:{view['id']}",
        )
    if too_late:
        builder.button(text="💬 Связаться с барбером", url=contact_url)
    builder.adjust(1)
    await message.answer("\n".join(lines), reply_markup=builder.as_markup())


@router.message(F.text == ClientMenu.MY_BOOKINGS, StateFilter(None))
async def open_my_bookings(message: Message, db: Database, state: FSMContext, config) -> None:
    await state.clear()
    await _render_bookings(message, db, config.BARBER_CONTACT_URL)


@router.callback_query(F.data.startswith("my:cancel:"))
async def cb_cancel(callback: CallbackQuery, db: Database) -> None:
    booking_id = int(callback.data.split(":")[2])
    view = await BookingsRepo(db).get_view(booking_id)
    if view is None:
        await callback.answer("Запись не найдена.", show_alert=True)
        return
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Да, отменить", callback_data=f"my:yes:{booking_id}")
    builder.button(text="❌ Нет", callback_data="my:back")
    builder.adjust(2)
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            f"Отменить запись на {fmt.esc(view['date_weekday'])} в {fmt.esc(view['slot'])}?",
            reply_markup=builder.as_markup(),
        )
    await callback.answer()


@router.callback_query(F.data == "my:back")
async def cb_back(callback: CallbackQuery, db: Database, config) -> None:
    if isinstance(callback.message, Message):
        await callback.message.answer("Ваши записи:")
        await _render_bookings(callback.message, db, config.BARBER_CONTACT_URL)
    await callback.answer()


@router.callback_query(F.data.startswith("my:yes:"))
async def cb_cancel_yes(
    callback: CallbackQuery,
    db: Database,
    state: FSMContext,
    config,
    booking_service: BookingService,
) -> None:
    booking_id = int(callback.data.split(":")[2])
    view = await BookingsRepo(db).get_view(booking_id)
    if view is None:
        await callback.answer("Запись не найдена.", show_alert=True)
        return
    # повторная проверка окна 4 часов на момент нажатия (A.6.4/A.6.5)
    visit_start = combine_msk(
        date.fromisoformat(view["date"]), parse_hhmm(view["slot_start"])
    )
    if not can_cancel(visit_start, now_msk()):
        if isinstance(callback.message, Message):
            await callback.message.answer(
                CANCEL_TOO_LATE_TEXT, reply_markup=_contact_keyboard(config.BARBER_CONTACT_URL)
            )
        await callback.answer()
        return
    cancelled = await booking_service.cancel_booking(
        booking_id, initiator="client", reason="отменена клиентом"
    )
    if isinstance(callback.message, Message):
        if cancelled:
            # подтверждение отмены придёт сообщением (сервис), обновляем список
            await callback.message.answer("Ваши записи:")
            await _render_bookings(callback.message, db, config.BARBER_CONTACT_URL)
        else:
            await callback.message.answer("Запись уже не активна.")
    await callback.answer()
