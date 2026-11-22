"""Фильтры ролей: IsAdmin / IsPrimaryAdmin (whitelist по Telegram ID).

База приходит из workflow-данных Dispatcher (dp["db"]).
"""

from aiogram.filters import BaseFilter
from aiogram.types import TelegramObject

from bot.db.database import Database
from bot.db.repositories.admins import AdminsRepo


class IsAdmin(BaseFilter):
    async def __call__(self, event: TelegramObject, db: Database) -> bool:
        user = getattr(event, "from_user", None)
        if user is None or user.is_bot:
            return False
        return await AdminsRepo(db).is_admin(user.id)


class IsPrimaryAdmin(BaseFilter):
    async def __call__(self, event: TelegramObject, db: Database) -> bool:
        user = getattr(event, "from_user", None)
        if user is None or user.is_bot:
            return False
        return await AdminsRepo(db).is_primary(user.id)
