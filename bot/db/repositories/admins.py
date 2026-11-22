"""Администраторы: whitelist по Telegram ID (раздел 3 ТЗ).

Основной админ задаётся при развёртывании (PRIMARY_ADMIN_ID) и единолично
управляет вторым админом. Оба админа имеют равные права на расписание,
услуги и записи; управление админами — только основной (п. 3.2 ТЗ).
"""

from typing import Optional

from bot.db.database import Database
from bot.utils.dates import utcnow_iso


class AdminsRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def ensure_primary(self, telegram_id: int) -> None:
        """Инициализация основного админа при первом старте (идемпотентно)."""
        async with self.db.transaction():
            await self.db.execute(
                "INSERT INTO users (telegram_id, username, created_at) VALUES (?, NULL, ?) "
                "ON CONFLICT(telegram_id) DO NOTHING",
                (telegram_id, utcnow_iso()),
            )
            user_id = await self.db.fetchval(
                "SELECT id FROM users WHERE telegram_id = ?", (telegram_id,)
            )
            await self.db.execute(
                "INSERT INTO admins (user_id, is_primary, added_by, added_at) "
                "VALUES (?, 1, NULL, ?) ON CONFLICT(user_id) DO NOTHING",
                (user_id, utcnow_iso()),
            )
            # Единственный основной админ — защита от дублей при смене конфигурации.
            await self.db.execute(
                "UPDATE admins SET is_primary = 0 "
                "WHERE is_primary = 1 AND user_id <> ?",
                (user_id,),
            )

    async def is_admin(self, telegram_id: int) -> bool:
        return await self.db.fetchval(
            "SELECT 1 FROM admins a JOIN users u ON u.id = a.user_id "
            "WHERE u.telegram_id = ?",
            (telegram_id,),
        ) is not None

    async def is_primary(self, telegram_id: int) -> bool:
        return await self.db.fetchval(
            "SELECT 1 FROM admins a JOIN users u ON u.id = a.user_id "
            "WHERE u.telegram_id = ? AND a.is_primary = 1",
            (telegram_id,),
        ) is not None

    async def add_admin(self, telegram_id: int, added_by: int) -> bool:
        """Добавляет второго админа. False — если он уже админ."""
        if await self.is_admin(telegram_id):
            return False
        async with self.db.transaction():
            await self.db.execute(
                "INSERT INTO users (telegram_id, username, created_at) VALUES (?, NULL, ?) "
                "ON CONFLICT(telegram_id) DO NOTHING",
                (telegram_id, utcnow_iso()),
            )
            user_id = await self.db.fetchval(
                "SELECT id FROM users WHERE telegram_id = ?", (telegram_id,)
            )
            await self.db.execute(
                "INSERT INTO admins (user_id, is_primary, added_by, added_at) "
                "VALUES (?, 0, ?, ?)",
                (user_id, added_by, utcnow_iso()),
            )
        return True

    async def remove_admin(self, telegram_id: int) -> bool:
        """Удаляет второго админа. Основного удалить нельзя — вернёт False."""
        if await self.is_primary(telegram_id):
            return False
        async with self.db.transaction():
            cursor = await self.db.execute(
                "DELETE FROM admins WHERE is_primary = 0 AND user_id = "
                "(SELECT id FROM users WHERE telegram_id = ?)",
                (telegram_id,),
            )
        return cursor.rowcount > 0

    async def list_admins(self) -> list:
        return await self.db.fetchall(
            "SELECT a.user_id, u.telegram_id, u.username, a.is_primary "
            "FROM admins a JOIN users u ON u.id = a.user_id "
            "ORDER BY a.is_primary DESC, u.telegram_id"
        )

    async def admin_tg_ids(self) -> list[int]:
        rows = await self.db.fetchall(
            "SELECT u.telegram_id FROM admins a JOIN users u ON u.id = a.user_id"
        )
        return [row["telegram_id"] for row in rows]

    async def primary_tg_id(self) -> Optional[int]:
        return await self.db.fetchval(
            "SELECT u.telegram_id FROM admins a JOIN users u ON u.id = a.user_id "
            "WHERE a.is_primary = 1"
        )
