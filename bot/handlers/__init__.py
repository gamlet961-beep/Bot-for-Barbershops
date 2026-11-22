"""Сборка всех роутеров бота.

Порядок важен: сначала админские роутеры (фильтр IsAdmin на уровне роутера),
затем клиентские. Для не-админа админские тексты проваливаются в клиентский
роутер, где остаётся только нейтральный ответ.
"""

from aiogram import Router


def setup_routers() -> list[Router]:
    from bot.handlers.admin import admins as admin_admins
    from bot.handlers.admin import bookings as admin_bookings
    from bot.handlers.admin import manual as admin_manual
    from bot.handlers.admin import schedule as admin_schedule
    from bot.handlers.admin import services as admin_services
    from bot.handlers.admin import start as admin_start
    from bot.handlers.client import cart as client_cart
    from bot.handlers.client import contact as client_contact
    from bot.handlers.client import datetime as client_datetime
    from bot.handlers.client import mybookings as client_mybookings
    from bot.handlers.client import services as client_services
    from bot.handlers.client import start as client_start

    return [
        admin_start.router,
        admin_admins.router,
        admin_schedule.router,
        admin_services.router,
        admin_bookings.router,
        admin_manual.router,
        client_services.router,
        client_datetime.router,
        client_cart.router,
        client_mybookings.router,
        client_contact.router,
        client_start.router,
    ]
