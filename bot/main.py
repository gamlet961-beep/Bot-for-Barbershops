"""Точка входа: polling + планировщик + health-сервер в одном event loop.

Запуск: python -m bot.main (или python bot/main.py — поддерживаются обе
формы: корень репозитория добавляется в sys.path перед импортами bot.*).
"""

import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from pathlib import Path

# Если точку входа исполнили как файл (python bot/main.py — так делает
# платформа при запуске «не тем» инструментом), sys.path[0] — каталог bot/,
# и пакет bot не виден. Корень репозитория спасает импорты bot.*.
_REPO_ROOT = str(Path(__file__).resolve().parents[1])
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from bot.config import Settings
from bot.db.database import Database
from bot.db.repositories.admins import AdminsRepo
from bot.handlers import setup_routers
from bot.health import start_health_server
from bot.middlewares.register_user import RegisterUserMiddleware
from bot.services.backup import BackupService
from bot.services.booking import BookingService
from bot.services.notifications import Notificator
from bot.services.privacy import PrivacyService
from bot.services.reminders import ReminderService
from bot.utils.dates import MSK

logger = logging.getLogger(__name__)


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        stream=sys.stdout,
    )
    logging.getLogger("apscheduler").setLevel(logging.WARNING)


async def main() -> None:
    config = Settings()
    setup_logging(config.LOG_LEVEL)

    if not config.BOT_TOKEN or not config.PRIMARY_ADMIN_ID:
        raise RuntimeError(
            "Не заданы обязательные переменные окружения: BOT_TOKEN, PRIMARY_ADMIN_ID"
        )

    db = Database(config.DATABASE_PATH)
    await db.connect()
    await db.migrate()

    # B.9: эфемерная файловая система PaaS — на чистом инстансе восстанавливаем
    # базу из последнего бэкапа (приватный GitHub-репозиторий).
    backup_service = BackupService(db, config.BACKUP_REPO_URL, config.BACKUP_GH_TOKEN)
    if (
        config.DATABASE_PATH != ":memory:"
        and backup_service.enabled()
        and await backup_service.is_database_empty()
    ):
        logger.info("База пуста — восстановление из удалённого бэкапа…")
        await db.close()
        restored = await backup_service.restore_to_file(config.DATABASE_PATH)
        await db.connect()
        await db.migrate()
        if restored:
            logger.info("База успешно восстановлена из бэкапа")
        else:
            logger.warning("Удалённый бэкап не найден — старт с чистой базой")

    await AdminsRepo(db).ensure_primary(config.PRIMARY_ADMIN_ID)

    bot = Bot(
        token=config.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=MemoryStorage())
    dp["db"] = db
    dp["config"] = config
    notificator = Notificator(bot, db)
    dp["notificator"] = notificator
    booking_service = BookingService(db, notificator)
    dp["booking_service"] = booking_service
    register_mw = RegisterUserMiddleware()
    dp.message.outer_middleware(register_mw)
    dp.callback_query.outer_middleware(register_mw)
    dp.include_routers(*setup_routers())

    # Планировщик напоминаний (п. 7, 9.1 ТЗ): восстановление при старте,
    # далее — диспетчеризация раз в минуту в таймзоне Europe/Moscow.
    reminder_service = ReminderService(db, notificator, booking_service)
    await reminder_service.reconcile()
    scheduler = AsyncIOScheduler(timezone=str(MSK))
    scheduler.add_job(
        reminder_service.dispatch_due,
        trigger="interval",
        minutes=1,
        id="reminders",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=30,
    )
    # Ежедневная анонимизация ПД старше 2 месяцев (п. 9.2 ТЗ) — 03:00 MSK
    scheduler.add_job(
        PrivacyService(db).anonymize_expired,
        trigger="cron",
        hour=3,
        minute=0,
        id="privacy",
        misfire_grace_time=3600,
    )
    # Снапшот базы каждые 15 минут с выгрузкой в GitHub (B.9)
    if backup_service.enabled():
        scheduler.add_job(
            backup_service.backup,
            trigger="interval",
            minutes=15,
            id="backup",
            max_instances=1,
            coalesce=True,
        )
    scheduler.start()
    logger.info("Планировщик напоминаний запущен (интервал 1 минута, MSK)")

    runner = await start_health_server(config.PORT)
    logger.info("Бот запускается (long polling)…")
    try:
        await dp.start_polling(bot)
    finally:
        scheduler.shutdown(wait=False)
        await runner.cleanup()
        await db.close()
        logger.info("Бот остановлен")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
