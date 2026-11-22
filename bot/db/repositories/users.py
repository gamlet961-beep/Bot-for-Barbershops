"""Пользователи (клиенты)."""

from typing import Optional

from bot.db.database import Database
from bot.utils.dates import utcnow_iso


class UsersRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def upsert_by_telegram(self, telegram_id: int, username: Optional[str]) -> int:
        """Регистрирует пользователя при первом контакте; обновляет username."""
        async with self.db.transaction():
            await self.db.execute(
                "INSERT INTO users (telegram_id, username, created_at) VALUES (?, ?, ?) "
                "ON CONFLICT(telegram_id) DO UPDATE SET username = excluded.username",
                (telegram_id, username, utcnow_iso()),
            )
        return await self.get_id_by_telegram(telegram_id)  # type: ignore[return-value]

    async def get_id_by_telegram(self, telegram_id: int) -> Optional[int]:
        row = await self.db.fetchone(
            "SELECT id FROM users WHERE telegram_id = ?", (telegram_id,)
        )
        return row["id"] if row else None

    async def get_by_telegram_id(self, telegram_id: int):
        return await self.db.fetchone(
            "SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)
        )

    async def get_by_phone(self, phone: str):
        return await self.db.fetchone("SELECT * FROM users WHERE phone = ?", (phone,))

    async def set_contact(self, user_id: int, name: str, phone: str) -> None:
        """Имя и телефон фиксируются при оформлении записи (связка tg_id + phone)."""
        async with self.db.transaction():
            await self.db.execute(
                "UPDATE users SET name = ?, phone = ? WHERE id = ?", (name, phone, user_id)
            )

    async def anonymize(self, user_id: int) -> None:
        async with self.db.transaction():
            await self.db.execute(
                "UPDATE users SET name = NULL, phone = NULL WHERE id = ?", (user_id,)
            )
