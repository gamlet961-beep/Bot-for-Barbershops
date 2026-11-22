"""Репозиторий услуг и категорий (ТЗ, п. 4.3).

Скрытие (soft delete): is_active = 0 — услуга исчезает из клиентского меню,
записи не затрагиваются (A.3.3–A.3.5 акта).
"""

from typing import Optional

from bot.db.database import Database

SERVICE_SELECT = (
    "SELECT s.*, c.name AS category_name FROM services s "
    "JOIN categories c ON c.id = s.category_id "
)


class ServicesRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def categories(self) -> list:
        return await self.db.fetchall(
            "SELECT * FROM categories ORDER BY sort_order, id"
        )

    async def list(self, only_active: bool = True, category_id: Optional[int] = None) -> list:
        where = ["1 = 1"]
        params: list = []
        if only_active:
            where.append("s.is_active = 1")
        if category_id is not None:
            where.append("s.category_id = ?")
            params.append(category_id)
        return await self.db.fetchall(
            SERVICE_SELECT + "WHERE " + " AND ".join(where) + " ORDER BY c.sort_order, s.sort_order, s.id",
            params,
        )

    async def active_by_category(self) -> dict[int, list]:
        """Активные услуги, сгруппированные по категориям (п. 5.2 ТЗ)."""
        rows = await self.list(only_active=True)
        grouped: dict[int, list] = {}
        for row in rows:
            grouped.setdefault(row["category_id"], []).append(row)
        return grouped

    async def get(self, service_id: int):
        return await self.db.fetchone(SERVICE_SELECT + "WHERE s.id = ?", (service_id,))

    async def create(
        self, *, category_id: int, name: str, description: Optional[str], price: int
    ) -> int:
        async with self.db.transaction():
            cursor = await self.db.execute(
                "INSERT INTO services (category_id, name, description, price, is_active, sort_order) "
                "VALUES (?, ?, ?, ?, 1, (SELECT COALESCE(MAX(sort_order), 0) + 1 FROM services))",
                (category_id, name, description, price),
            )
        return cursor.lastrowid  # type: ignore[return-value]

    async def update(
        self,
        service_id: int,
        *,
        name: Optional[str] = None,
        description: Optional[str] = None,
        price: Optional[int] = None,
        category_id: Optional[int] = None,
    ) -> bool:
        sets, params = [], []
        if name is not None:
            sets.append("name = ?")
            params.append(name)
        if description is not None:
            sets.append("description = ?")
            params.append(description)
        if price is not None:
            sets.append("price = ?")
            params.append(price)
        if category_id is not None:
            sets.append("category_id = ?")
            params.append(category_id)
        if not sets:
            return False
        params.append(service_id)
        async with self.db.transaction():
            cursor = await self.db.execute(
                f"UPDATE services SET {', '.join(sets)} WHERE id = ?", params  # noqa: S608
            )
        return cursor.rowcount > 0

    async def set_active(self, service_id: int, is_active: bool) -> bool:
        async with self.db.transaction():
            cursor = await self.db.execute(
                "UPDATE services SET is_active = ? WHERE id = ?",
                (int(is_active), service_id),
            )
        return cursor.rowcount > 0
