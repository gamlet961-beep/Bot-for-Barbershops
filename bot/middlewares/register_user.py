"""Регистрация пользователей: upsert по telegram_id при каждом входящем событии."""

import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from bot.db.database import Database
from bot.db.repositories.users import UsersRepo

logger = logging.getLogger(__name__)


class RegisterUserMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        if user is not None and not user.is_bot:
            db: Database = data["db"]
            await UsersRepo(db).upsert_by_telegram(user.id, user.username)
            logger.debug("Пользователь зарегистрирован/обновлён: %s", user.id)
        return await handler(event, data)
