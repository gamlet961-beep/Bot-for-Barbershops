"""Health-сервер: эндпоинт доступности для PaaS и внешнего keep-alive пинга.

Запускается в том же event loop, что и бот (принцип «один асинхронный процесс»).
"""

import logging

from aiohttp import web

logger = logging.getLogger(__name__)


async def _health(_request: web.Request) -> web.Response:
    return web.json_response({"status": "ok"})


async def start_health_server(port: int) -> web.AppRunner:
    app = web.Application()
    app.router.add_get("/health", _health)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info("Health-сервер запущен: http://0.0.0.0:%s/health", port)
    return runner
