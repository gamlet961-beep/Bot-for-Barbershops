"""Проверка токена бота перед запуском: getMe + снятие webhook.

Токен берётся из аргумента командной строки, переменной окружения BOT_TOKEN
или файла .env в текущем каталоге (приоритет — в этом порядке).
Использует только стандартную библиотеку — работает до установки зависимостей.
Выход: 0 — токен живой, webhook снят, можно запускать бот; 1 — ошибка.
"""

import json
import os
import sys
import urllib.error
import urllib.request

API_BASE = "https://api.telegram.org"


def call_api(token: str, method: str) -> dict:
    """Вызвать метод Telegram Bot API и вернуть разобранный JSON-ответ."""
    request = urllib.request.Request(f"{API_BASE}/bot{token}/{method}", method="GET")
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def read_env_file(path: str = ".env") -> dict:
    """Прочитать KEY=VALUE из .env в текущем каталоге (без зависимостей)."""
    values: dict = {}
    try:
        with open(path, encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                values[key.strip()] = value.strip().strip('"').strip("'")
    except OSError:
        pass
    return values


def main() -> int:
    dotenv = read_env_file()
    token = sys.argv[1] if len(sys.argv) > 1 else (
        os.environ.get("BOT_TOKEN", "") or dotenv.get("BOT_TOKEN", "")
    )
    if not token:
        print("Токен не задан. Задайте его одним из способов:")
        print("  1) впишите BOT_TOKEN=... в файл .env в корне проекта (шаблон — .env.example);")
        print('  2) переменной окружения: BOT_TOKEN="123456:ABC..." python check_bot.py;')
        print("  3) аргументом: python check_bot.py 123456:ABC...")
        return 1

    try:
        info = call_api(token, "getMe")
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            print("✗ Токен неверен или отозван (HTTP 401).")
            print("  Скопируйте заново: @BotFather → /mybots → бот → API Token.")
        else:
            print(f"✗ Telegram API ответил ошибкой HTTP {exc.code}.")
        return 1
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        print(f"✗ Нет соединения с api.telegram.org: {exc}")
        print("  Проверьте интернет/прокси и повторите.")
        return 1

    if not info.get("ok"):
        print(f"✗ Неожиданный ответ Telegram: {info}")
        return 1

    bot = info["result"]
    print(f"✓ Токен живой. Бот: {bot.get('first_name')} (@{bot.get('username')}, id {bot.get('id')})")

    # Webhook и long polling несовместимы: если токен раньше использовался
    # с webhook, polling не будет получать обновления. Снимаем принудительно.
    try:
        webhook = call_api(token, "deleteWebhook")
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError):
        print("! Не удалось снять webhook — если бот не отвечает, повторите позже.")
        return 0
    if webhook.get("ok"):
        print("✓ Webhook снят, long polling будет работать.")
    print("Всё готово к запуску:  python -m bot.main")
    return 0


if __name__ == "__main__":
    sys.exit(main())
