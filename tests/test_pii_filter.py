"""Tests for :mod:`app.services.pii_filter`.

The filter is a pure module, so these tests need no database, no app settings
and no web app. They pin down the verdicts for every rule code, the common
obfuscation tricks, the false-positive boundary cases, and the shape of
``FilterResult`` (redaction and truncated match snippets).
"""

import sys
from pathlib import Path

import pytest

# The tests live in ``tests/`` without a package marker, so make the project
# root importable when pytest is invoked as ``pytest tests/test_pii_filter.py``.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.pii_filter import (  # noqa: E402
    MAX_MATCH_FRAGMENT,
    PLACEHOLDER,
    Verdict,
    check_text,
)


@pytest.mark.parametrize(
    "text",
    [
        "+7 912 345-67-89",
        "8 (912) 345 67 89",
        "89123456789",
        "+79123456789",
        "912 345-67-89",
    ],
)
def test_phone_is_blocked(text):
    result = check_text(f"Позвони мне: {text}")
    assert result.verdict is Verdict.BLOCK
    assert "phone" in result.codes


@pytest.mark.parametrize(
    "text",
    [
        "ivan.petrov@mail.ru",
        "example @ mail.ru",  # spaces around the at-sign
        "Пиши на test@school.рф",
    ],
)
def test_email_is_blocked(text):
    result = check_text(text)
    assert result.verdict is Verdict.BLOCK
    assert "email" in result.codes


def test_email_does_not_also_report_a_link():
    # The domain inside an address must not be double-reported as a bare link.
    result = check_text("ivan.petrov@mail.ru")
    assert result.codes == frozenset({"email"})


@pytest.mark.parametrize(
    "text",
    [
        "Заходи на school-site.ru",
        "https://example.com/page",
        "www.example.org",
        "SITE.RU",  # upper case domain
    ],
)
def test_link_is_blocked(text):
    result = check_text(text)
    assert result.verdict is Verdict.BLOCK
    assert "link" in result.codes


@pytest.mark.parametrize(
    "text",
    [
        "Мой ник @vanya_petrov",
        "Пишите в t.me/vanya",
        "Я в vk.com/ivanov",
        "телеграм: vanya1990",
    ],
)
def test_messenger_is_blocked(text):
    result = check_text(text)
    assert result.verdict is Verdict.BLOCK
    assert "messenger" in result.codes


@pytest.mark.parametrize(
    "text",
    [
        "Иван Петрович Сидоров",
        "Меня зовут Анна Петрова",
        "Это Пётр Иванович",
    ],
)
def test_full_name_is_flagged(text):
    result = check_text(text)
    assert result.verdict is Verdict.FLAG
    assert "full_name" in result.codes


@pytest.mark.parametrize(
    "text",
    [
        "Я живу на ул. Ленина",
        "Проспект Мира, дом 10",
        "Квартира 5 на третьем этаже",
        "Индекс 123456",
    ],
)
def test_address_is_flagged(text):
    result = check_text(text)
    assert result.verdict is Verdict.FLAG
    assert "address" in result.codes


@pytest.mark.parametrize(
    "text",
    [
        "Я учусь в 7А",
        "Класс 11 Б",
        "Мы в 3 классе",
        "Группа 12",
        "Ученик 9-а",
    ],
)
def test_class_group_is_flagged(text):
    result = check_text(text)
    assert result.verdict is Verdict.FLAG
    assert "class_group" in result.codes


@pytest.mark.parametrize(
    "text",
    [
        "Мы решали задачу 5 раз, потом ещё 3-4 минуты ждали",
        "Цена билета 1500 рублей, начало в 8 часов",
        "Я учусь в этой школе давно, нравится",
        "Скинул в телеграм",
        "Он Иванов",  # a lone surname, no second name word -> not a full name
    ],
)
def test_false_positives_pass(text):
    result = check_text(text)
    assert result.verdict is Verdict.PASS
    assert result.codes == frozenset()


def test_invisible_characters_do_not_hide_a_phone():
    text = "Мой номер +7\u200b912 345 67 89"  # zero-width space after +7
    result = check_text(text)
    assert result.verdict is Verdict.BLOCK
    assert "phone" in result.codes


def test_full_name_plus_phone_blocks_and_reports_both_codes():
    result = check_text("Мой номер +79123456789, зовут Иван Петров")
    assert result.verdict is Verdict.BLOCK
    assert result.codes == frozenset({"phone", "full_name"})


def test_clean_text_redacts_soft_fragment_and_keeps_the_rest():
    result = check_text("Меня зовут Иван Петров, мне 15 лет")
    assert result.verdict is Verdict.FLAG
    assert result.codes == frozenset({"full_name"})
    assert result.clean_text == f"Меня зовут {PLACEHOLDER}, мне 15 лет"


def test_full_name_plus_phone_blocks_and_reports_both_codes_clean_text():
    result = check_text("Мой номер +79123456789, зовут Иван Петров")
    assert result.clean_text == f"Мой номер {PLACEHOLDER}, зовут {PLACEHOLDER}"


def test_matches_are_truncated_and_never_the_whole_text():
    text = "Мой адрес: verylonglocalpart.aaaaaaaaaaaaaaaaaaaa@mail.ru"
    result = check_text(text)
    assert result.matches
    for match in result.matches:
        assert len(match.fragment) <= MAX_MATCH_FRAGMENT
        assert match.fragment != text


def test_pass_result_has_no_codes_and_keeps_text():
    text = "Мы решали задачу 5 раз, потом ещё 3-4 минуты ждали"
    result = check_text(text)
    assert result.verdict is Verdict.PASS
    assert result.matches == ()
    assert result.clean_text == text
