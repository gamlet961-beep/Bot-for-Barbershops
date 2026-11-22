"""Фаза 2: валидация и нормализация телефона (п. 5.5 ТЗ; A.5.7 акта)."""

import pytest

from bot.utils.phone import normalize_phone


@pytest.mark.parametrize(
    "raw",
    [
        "+79261234567",
        "79261234567",
        "89261234567",
        "9261234567",
        "+7 926 123-45-67",
        "8 (926) 123-45-67",
        "926-123-45-67",
        " +7(926)1234567 ",
    ],
)
def test_valid_phones_normalized(raw):
    assert normalize_phone(raw) == "+79261234567"


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        "hello",
        "12345",
        "+7926123456",      # 10 цифр после +7 — мало
        "+792612345678",    # 12 цифр — много
        "892612345678",     # 12 цифр с восьмёркой
        "8",                # только восьмёрка
        "+",
        "926123456",        # 9 цифр без префикса
    ],
)
def test_invalid_phones_rejected(raw):
    assert normalize_phone(raw) is None
