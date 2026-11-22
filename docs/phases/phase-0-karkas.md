# Фаза 0 — Каркас проекта

> Отчёт по плану разработки (раздел 5.2). Статус: **завершена** 21.11.2026.
> Сверка с актом приёма-передачи: см. блок «Соответствие акту приёмки».

## Что сделано

- **Репозиторий и зависимости.** Структура каталогов зафиксирована по разделу 3.2 плана
  (`bot/`, `tests/`, `deploy/`, `docs/`). Версии закреплены точечно в `requirements.txt`:
  aiogram 3.33.0, aiosqlite 0.22.1, APScheduler 3.11.3, pydantic 2.14.4,
  pydantic-settings 2.16.3, aiohttp 3.14.5 (все проверены по PyPI 21.11.2026).
- **Конфигурация** (`bot/config.py`): pydantic-settings, все переменные окружения из
  Таблицы 4 плана (BOT_TOKEN, PRIMARY_ADMIN_ID, BARBER_CONTACT_URL, DATABASE_PATH,
  BOOKING_HORIZON_DAYS, PORT, BACKUP_REPO_URL, BACKUP_GH_TOKEN, LOG_LEVEL).
  Валидация: горизонт записи — положительное число.
- **Слой БД** (`bot/db/database.py`): единое соединение aiosqlite с
  `isolation_level=None` (ручные транзакции), PRAGMA `journal_mode=WAL`,
  `busy_timeout=5000`, `foreign_keys=ON`. Раннер нумерованных SQL-миграций
  с таблицей `schema_version`; транзакция `BEGIN IMMEDIATE` доступна сервисному
  слою как `async with db.transaction()`.
- **Миграция 001** (`bot/db/migrations/001_init.sql`): полная схема из Таблицы 3 плана —
  users, admins, schedule_templates, schedule_exceptions, blocked_slots, categories,
  services, bookings, booking_slots, booking_services, notifications.
  Ключевые детали:
  - частичный уникальный индекс `ux_active_slot (date, slot_start) WHERE is_active = 1` —
    вторая линия защиты от двойного бронирования (п. 6.2 ТЗ);
  - `UNIQUE (booking_id, type)` в notifications — идемпотентность напоминаний (п. 9.1 ТЗ);
  - снимки `client_name`/`client_phone` и `pd_consent_at`, `is_anonymized` в bookings —
    требования 152-ФЗ (раздел 9 ТЗ);
  - предзаполнение категорий «Стрижка» и «Борода и усы» (допущение № 2 плана).
- **Утилиты**: `bot/utils/dates.py` (единый пояс Europe/Moscow, форматирование,
  парсинг дат/времени, окно «2 месяца назад», неделя пн–вс) и `bot/utils/phone.py`
  (нормализация `+7…/8…/9…` → `+7XXXXXXXXXX`, п. 5.5 ТЗ). Активно используются с фазы 2.
- **Health-сервер** (`bot/health.py`): aiohttp, `GET /health` → `{"status": "ok"}`,
  порт из `PORT` — контроль живости для PaaS и внешний keep-alive пинг.
- **Точка входа** (`bot/main.py`): один event loop — long polling aiogram +
  health-сервер; MemoryStorage; логирование в stdout. Планировщик подключается на фазе 7.
- **Dockerfile** (`deploy/Dockerfile`): python:3.12-slim, воспроизводимая сборка.
- **Тестовая база**: pytest + pytest-asyncio (auto), фикстура базы в памяти с миграциями.

## Результат фазы (критерий готовности по плану)

Бот запускается локально (`python -m bot.main`), миграции применяются автоматически,
`/health` отвечает 200, `/start` присылает заглушку, БД создаётся в WAL-режиме.
Проверено прогоном тестов (см. ниже) и smoke-импортом всех модулей каркаса.

## Тесты

```
6 passed in 0.03s   (tests/test_migrations.py)
```

Покрытие: наличие всех 12 таблиц, предзаполнение категорий, версия схемы,
включённые foreign_keys, идемпотентность миграций, наличие индекса `ux_active_slot`.

## Соответствие акту приёмки

| Пункт акта | Статус | Комментарий |
|---|---|---|
| B.1 (работа на PaaS без вмешательства) | 🟡 основа | health-эндпоинт, WAL, миграции при старте, Dockerfile |
| B.9 (сохранность SQLite) | 🟡 основа | WAL-режим; подсистема бэкапов — фаза 9 |
| B.12 (README, развёртывание с нуля) | 🟡 основа | Dockerfile и конфиг готовы; README — фаза 9 |

Фаза 0 не закрывает пункты акта напрямую — это инфраструктурный фундамент блоков A и B.

## Зафиксированные решения

1. Единица развёртывания — один процесс (polling + планировщик + health): проще на PaaS,
   нет межпроцессной синхронизации (принцип 1.2 плана).
2. `isolation_level=None` + явные `BEGIN IMMEDIATE`: сериализация записи слота на уровне
   БД до прикладных проверок.
3. Пустой `BOT_TOKEN`/`PRIMARY_ADMIN_ID` не проходят дальше старта — понятная ошибка
   вместо загадочного падения aiogram.
