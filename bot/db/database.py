"""Обёртка над aiosqlite.

Особенности:
- единое соединение на процесс (нагрузка мала: ~5 записей в день);
- PRAGMA WAL + busy_timeout + foreign_keys при подключении;
- ручное управление транзакциями (isolation_level=None): сервисный слой
  использует ``async with db.transaction()`` = BEGIN IMMEDIATE ... COMMIT/ROLLBACK
  (защита от двойного бронирования, п. 6.2 ТЗ);
- нумерованные SQL-миграции из каталога migrations (NNN_имя.sql), версия — в schema_version.
"""

import asyncio
import logging
import re
from pathlib import Path
from typing import Any, Iterable, Optional

import aiosqlite

logger = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


class Database:
    """Единое соединение aiosqlite на процесс.

    Транзакции сериализуются процессным asyncio.Lock: параллельные запросы
    обрабатываются последовательно (п. 6.2 ТЗ, B.4 акта). Вложенные транзакции
    не поддерживаются — сервисный слой открывает транзакцию один раз.
    """

    def __init__(self, path: str = "data/barber.db") -> None:
        self.path = path
        self._conn: Optional[aiosqlite.Connection] = None
        self._tx_lock = asyncio.Lock()

    async def connect(self) -> None:
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self.path, isolation_level=None)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA busy_timeout=5000")
        await self._conn.execute("PRAGMA foreign_keys=ON")
        logger.info("База данных подключена: %s", self.path)

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database.connect() не был вызван")
        return self._conn

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None
            logger.info("Соединение с базой данных закрыто")

    # ── примитивы запросов ──

    async def execute(self, sql: str, params: Iterable[Any] = ()) -> aiosqlite.Cursor:
        return await self.conn.execute(sql, tuple(params))

    async def fetchone(self, sql: str, params: Iterable[Any] = ()) -> Optional[aiosqlite.Row]:
        async with self.conn.execute(sql, tuple(params)) as cur:
            return await cur.fetchone()

    async def fetchall(self, sql: str, params: Iterable[Any] = ()) -> list[aiosqlite.Row]:
        async with self.conn.execute(sql, tuple(params)) as cur:
            return await cur.fetchall()

    async def fetchval(self, sql: str, params: Iterable[Any] = ()) -> Any:
        row = await self.fetchone(sql, params)
        return row[0] if row is not None else None

    def transaction(self) -> "_Transaction":
        """Контекст-менеджер транзакции: BEGIN IMMEDIATE / COMMIT / ROLLBACK.

        Одновременно может быть открыта только одна транзакция (Lock):
        конкурентные записи ждут своей очереди.
        """
        return _Transaction(self.conn, self._tx_lock)

    # ── миграции ──

    async def migrate(self) -> None:
        await self.conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)"
        )
        current = (await self.fetchval("SELECT MAX(version) FROM schema_version")) or 0
        applied = 0
        for sql_file in sorted(MIGRATIONS_DIR.glob("*.sql")):
            match = re.match(r"(\d+)_", sql_file.name)
            if not match:
                logger.warning("Пропущен файл миграции с неожидаемым именем: %s", sql_file.name)
                continue
            version = int(match.group(1))
            if version <= current:
                continue
            script = sql_file.read_text(encoding="utf-8")
            async with self.transaction():
                for statement in script.split(";"):
                    if statement.strip():
                        await self.conn.execute(statement)
                await self.conn.execute(
                    "INSERT INTO schema_version (version) VALUES (?)", (version,)
                )
            applied += 1
            logger.info("Применена миграция: %s (версия %d)", sql_file.name, version)
        if not applied:
            logger.info("Схема базы данных актуальна, версия %d", current)


class _Transaction:
    """BEGIN IMMEDIATE — захват записи на время транзакции (п. 6.2 ТЗ).

    Дополнительно сериализует конкурентные транзакции процесса: Lock
    удерживается от BEGIN до COMMIT/ROLLBACK (B.4 акта).
    """

    def __init__(self, conn: aiosqlite.Connection, lock: asyncio.Lock) -> None:
        self._conn = conn
        self._lock = lock

    async def __aenter__(self) -> aiosqlite.Connection:
        await self._lock.acquire()
        try:
            await self._conn.execute("BEGIN IMMEDIATE")
        except Exception:
            self._lock.release()
            raise
        return self._conn

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        try:
            if exc_type is None:
                await self._conn.execute("COMMIT")
            else:
                await self._conn.execute("ROLLBACK")
        finally:
            self._lock.release()
        return False
