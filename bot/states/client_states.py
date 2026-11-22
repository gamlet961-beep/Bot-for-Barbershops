"""Состояния FSM клиентских сценариев (раздел 5 ТЗ)."""

from aiogram.fsm.state import State, StatesGroup


class ClientBookingStates(StatesGroup):
    """Сценарий записи: услуги → дата и слот → корзина → оформление."""

    choosing_services = State()   # выбор услуг: категории, toggle, «Убрать»
    choosing_datetime = State()   # неделя / другая дата / выбор слота
    entering_name = State()       # ввод имени
    entering_phone = State()      # ввод телефона с валидацией
    confirming = State()          # согласие на обработку ПД + «Записаться»
