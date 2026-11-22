-- Миграция 001: полная схема данных (уточнение раздела 8 ТЗ, см. docs/DATA_MODEL.md)

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id INTEGER NOT NULL UNIQUE,
    username TEXT,
    name TEXT,
    phone TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS admins (
    user_id INTEGER PRIMARY KEY REFERENCES users(id),
    is_primary INTEGER NOT NULL DEFAULT 0,
    added_by INTEGER,
    added_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS schedule_templates (
    weekday INTEGER PRIMARY KEY CHECK (weekday BETWEEN 0 AND 6),
    is_working_day INTEGER NOT NULL DEFAULT 0,
    start_time TEXT,
    end_time TEXT
);

CREATE TABLE IF NOT EXISTS schedule_exceptions (
    date TEXT PRIMARY KEY,
    is_working_day INTEGER NOT NULL DEFAULT 0,
    start_time TEXT,
    end_time TEXT,
    note TEXT
);

CREATE TABLE IF NOT EXISTS blocked_slots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    slot_start TEXT NOT NULL,
    slot_end TEXT NOT NULL,
    reason TEXT,
    UNIQUE (date, slot_start)
);

CREATE TABLE IF NOT EXISTS categories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    sort_order INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS services (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category_id INTEGER NOT NULL REFERENCES categories(id),
    name TEXT NOT NULL,
    description TEXT,
    price INTEGER NOT NULL CHECK (price >= 0),
    is_active INTEGER NOT NULL DEFAULT 1,
    sort_order INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS bookings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id INTEGER REFERENCES users(id),
    client_name TEXT,
    client_phone TEXT,
    created_by TEXT NOT NULL DEFAULT 'client' CHECK (created_by IN ('client', 'admin')),
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'cancelled')),
    total_price INTEGER NOT NULL DEFAULT 0,
    pd_consent_at TEXT,
    is_anonymized INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    cancelled_at TEXT,
    cancel_reason TEXT,
    cancel_initiator TEXT
);

CREATE TABLE IF NOT EXISTS booking_slots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    booking_id INTEGER NOT NULL REFERENCES bookings(id) ON DELETE CASCADE,
    date TEXT NOT NULL,
    slot_start TEXT NOT NULL,
    slot_end TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1
);

-- Частичный уникальный индекс: среди активных слотов пара (дата, начало) уникальна.
-- Вторая линия защиты от двойного бронирования (п. 6.2 ТЗ).
CREATE UNIQUE INDEX IF NOT EXISTS ux_active_slot
    ON booking_slots (date, slot_start) WHERE is_active = 1;

CREATE INDEX IF NOT EXISTS ix_slots_date ON booking_slots (date);

CREATE TABLE IF NOT EXISTS booking_services (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    booking_id INTEGER NOT NULL REFERENCES bookings(id) ON DELETE CASCADE,
    service_id INTEGER REFERENCES services(id),
    name_at_booking TEXT NOT NULL,
    price_at_booking INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    booking_id INTEGER REFERENCES bookings(id) ON DELETE CASCADE,
    type TEXT NOT NULL,
    recipient_tg_id INTEGER,
    planned_at TEXT,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'sending', 'sent', 'failed', 'skipped')),
    sent_at TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (booking_id, type)
);

CREATE INDEX IF NOT EXISTS ix_notifications_due ON notifications (status, planned_at);

-- Предзаполнение категорий услуг (допущение № 2 плана разработки)
INSERT OR IGNORE INTO categories (id, name, sort_order) VALUES (1, 'Стрижка', 1);
INSERT OR IGNORE INTO categories (id, name, sort_order) VALUES (2, 'Борода и усы', 2);
