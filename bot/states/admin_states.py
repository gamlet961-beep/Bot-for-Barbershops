"""Состояния FSM админских сценариев."""

from aiogram.fsm.state import State, StatesGroup


class AdminManageStates(StatesGroup):
    """Управление администраторами (только основной админ)."""

    waiting_add_id = State()
    waiting_remove_id = State()


class ScheduleStates(StatesGroup):
    """Редактор расписания (п. 4.2 ТЗ)."""

    waiting_tpl_hours = State()      # ввод часов шаблона дня недели
    waiting_exc_date = State()       # ввод даты исключения
    waiting_exc_action = State()     # выбор: изменить часы / выходной
    waiting_exc_hours = State()      # ввод часов исключения
    waiting_block_date = State()     # ввод даты блокировки слота
    waiting_block_slot = State()     # выбор слота из сетки дня
    waiting_cancel_date = State()    # ввод даты отмены рабочего дня
    waiting_cancel_confirm = State() # подтверждение Да/Нет при наличии записей


class ServiceStates(StatesGroup):
    """Редактор услуг (п. 4.3 ТЗ)."""

    waiting_name = State()           # добавление: название
    waiting_price = State()          # добавление: цена
    waiting_description = State()    # добавление: описание
    waiting_category = State()       # добавление: выбор категории
    waiting_edit_name = State()      # редактирование названия
    waiting_edit_price = State()     # редактирование цены
    waiting_edit_description = State()  # редактирование описания


class BookingsStates(StatesGroup):
    """Раздел «Записи» (п. 4.4 ТЗ)."""

    waiting_date = State()           # произвольная дата
    waiting_range_from = State()     # диапазон: начало
    waiting_range_to = State()       # диапазон: конец
    waiting_search = State()         # поиск по имени/телефону


class ManualBookingStates(StatesGroup):
    """Ручное создание записи админом (п. 4.5 ТЗ)."""

    choosing_services = State()
    choosing_datetime = State()
    entering_name = State()
    entering_phone = State()
    confirming = State()
