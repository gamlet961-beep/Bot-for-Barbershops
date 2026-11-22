"""Резервное копирование SQLite на PaaS с эфемерной файловой системой.

Каждые 15 минут: снапшот базы (VACUUM INTO) → выгрузка в приватный
GitHub-репозиторий через Contents API (создание или обновление файла).
При старте процесса: если база пуста, а удалённый бэкап существует, —
восстановление из последней копии (B.9 акта).

Транспорт GitHub выделен в переопределяемые методы (_remote_sha/_put_remote/
_download_remote) — в тестах подменяется фейком без сети.
"""

import base64
import logging
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import aiohttp

from bot.db.database import Database

logger = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"


class BackupService:
    def __init__(
        self,
        db: Database,
        repo: str,
        token: str,
        branch: str = "main",
        remote_path: str = "backups/barber.db",
    ) -> None:
        self.db = db
        self.repo = repo.strip().strip("/")
        self.token = token.strip()
        self.branch = branch
        self.remote_path = remote_path

    def enabled(self) -> bool:
        return bool(self.repo and self.token)

    # ── состояние базы ──

    async def is_database_empty(self) -> bool:
        """Пустая база = нет ни одной записи (сигнал к восстановлению)."""
        count = await self.db.fetchval("SELECT COUNT(*) FROM bookings")
        return (count or 0) == 0

    # ── снапшот ──

    async def create_snapshot(self) -> bytes:
        """VACUSS INTO — консистентная копия живой базы (WAL-safe)."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            await self.db.conn.execute(f"VACUUM INTO '{tmp_path}'")
            return Path(tmp_path).read_bytes()
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    async def backup(self) -> str:
        """Выгрузка снапшота: 'uploaded' | 'disabled' | 'failed'."""
        if not self.enabled():
            return "disabled"
        try:
            data = await self.create_snapshot()
            sha = await self._remote_sha()
            await self._put_remote(data, sha)
            logger.info("Бэкап базы выгружен в %s@%s (%d байт)", self.repo, self.remote_path, len(data))
            return "uploaded"
        except Exception as exc:  # noqa: BLE001 — бэкап не должен ронять бота
            logger.error("Ошибка бэкапа: %s", exc)
            return "failed"

    async def restore_to_file(self, path: str) -> bool:
        """Скачивает удалённый бэкап и записывает в файл базы (база закрыта)."""
        data = await self._download_remote()
        if data is None:
            return False
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        logger.info("База восстановлена из бэкапа: %s (%d байт)", path, len(data))
        return True

    # ── транспорт GitHub Contents API (переопределяется в тестах) ──

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    async def _remote_sha(self) -> str | None:
        url = f"{GITHUB_API}/repos/{self.repo}/contents/{self.remote_path}?ref={self.branch}"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=self._headers()) as resp:
                if resp.status == 404:
                    return None
                resp.raise_for_status()
                payload = await resp.json()
                return payload.get("sha")

    async def _put_remote(self, data: bytes, sha: str | None) -> None:
        url = f"{GITHUB_API}/repos/{self.repo}/contents/{self.remote_path}"
        body = {
            "message": f"Бэкап базы {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC",
            "content": base64.b64encode(data).decode("ascii"),
            "branch": self.branch,
        }
        if sha:
            body["sha"] = sha
        async with aiohttp.ClientSession() as session:
            async with session.put(url, headers=self._headers(), json=body) as resp:
                if resp.status not in (200, 201):
                    raise RuntimeError(f"GitHub PUT вернул {resp.status}: {await resp.text()}")

    async def _download_remote(self) -> bytes | None:
        url = f"{GITHUB_API}/repos/{self.repo}/contents/{self.remote_path}?ref={self.branch}"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=self._headers()) as resp:
                if resp.status == 404:
                    return None
                resp.raise_for_status()
                payload = await resp.json()
                return base64.b64decode(payload["content"])
