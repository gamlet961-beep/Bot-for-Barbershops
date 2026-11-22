"""Единый отправитель сообщений + рассылка админам.

Обрабатывает ошибки Telegram API без падения бота (B.6 акта):
- «bot was blocked by the user» -> результат 'blocked' (реакция — автоотмена
  записи — подключается на фазе 7 в диспетчере напоминаний и отменах);
- лимит 429 -> пауза и повтор, затем 'failed';
- прочие ошибки -> 'failed' с записью в лог.

В тестовом режиме (bot=None) сообщения логируются и считаются отправленными.
"""

import asyncio
import logging

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError, TelegramRetryAfter

from bot.db.database import Database
from bot.db.repositories.admins import AdminsRepo

logger = logging.getLogger(__name__)


class Notificator:
    def __init__(self, bot: Bot | None, db: Database) -> None:
        self.bot = bot
        self.db = db

    async def send_message(self, chat_id: int, text: str) -> str:
        """Отправка одному получателю. Возвращает 'sent' | 'blocked' | 'failed'."""
        if self.bot is None:
            logger.info("[БЕЗ ОТПРАВКИ] chat_id=%s: %s", chat_id, text.replace("\n", " | ")[:120])
            return "sent"
        try:
            await self.bot.send_message(chat_id=chat_id, text=text)
            return "sent"
        except TelegramForbiddenError:
            logger.warning("Бот заблокирован пользователем %s", chat_id)
            return "blocked"
        except TelegramRetryAfter as exc:
            logger.warning("Лимит Telegram (429): пауза %s c", exc.retry_after)
            await asyncio.sleep(exc.retry_after + 1)
            try:
                await self.bot.send_message(chat_id=chat_id, text=text)
                return "sent"
            except TelegramForbiddenError:
                return "blocked"
            except TelegramAPIError:
                return "failed"
        except TelegramAPIError as exc:
            logger.error("Ошибка Telegram API при отправке chat_id=%s: %s", chat_id, exc)
            return "failed"

    async def notify_admins(self, text: str) -> None:
        """Рассылка всем администраторам (получатель «Админ(ы)» из ТЗ, п. 7.1)."""
        for tg_id in await AdminsRepo(self.db).admin_tg_ids():
            await self.send_message(tg_id, text)
