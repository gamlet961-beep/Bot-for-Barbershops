"""Валидация и нормализация телефона (ТЗ, п. 5.5).

Принимаются форматы: +7XXXXXXXXXX, 8XXXXXXXXXX, 9XXXXXXXXXX
(плюс разделители: пробелы, дефисы, скобки).
Нормализация к формату +7XXXXXXXXXX.
"""

import re

PHONE_INVALID_TEXT = (
    "Неверный формат телефона. Введите номер в одном из форматов: "
    "+7XXXXXXXXXX, 8XXXXXXXXXX или 9XXXXXXXXXX."
)


def normalize_phone(raw: str) -> str | None:
    """Приводит введённый номер к +7XXXXXXXXXX; при ошибке возвращает None."""
    cleaned = re.sub(r"\D", "", raw)
    if not cleaned:
        return None
    if raw.strip().startswith("+7"):
        pass  # уже в международном формате без «+»
    elif cleaned.startswith("8") and len(cleaned) == 11:
        cleaned = "7" + cleaned[1:]
    elif cleaned.startswith("9") and len(cleaned) == 10:
        cleaned = "7" + cleaned
    if re.fullmatch(r"7\d{10}", cleaned):
        return "+" + cleaned
    return None
