"""Подписи киберспорта: игры, бейджи, статусы турнира.

Те же правила, что у модерации и жалоб: код живёт в базе, подпись -- здесь,
шаблон перебирает словарь и не знает ни одного названия. Тест
``tests/test_esports.py`` не даёт словарям разъехаться с кодами моделей.
"""

from app.models.esports import BADGE_CODES, GAME_CODES, TOURNAMENT_STATUSES

GAME_LABELS: dict[str, str] = {
    "cs2": "Counter-Strike 2",
    "brawl": "Brawl Stars",
}

GAME_SHORT: dict[str, str] = {
    "cs2": "CS2",
    "brawl": "Brawl Stars",
}

BADGE_LABELS: dict[str, str] = {
    "mvp": "MVP",
    "captain": "Капитан",
    "winner": "Победитель",
}

STATUS_LABELS: dict[str, str] = {
    "upcoming": "Скоро",
    "live": "Идёт",
    "finished": "Завершён",
}


def game_label(code: str) -> str:
    return GAME_LABELS.get(code, code)


def badge_label(code: str) -> str:
    # Неизвестный код не роняет страницу: новая награда в базе должна
    # показываться, даже если подпись к ней ещё не завели.
    return BADGE_LABELS.get(code, code)


def status_label(code: str) -> str:
    return STATUS_LABELS.get(code, code)


# Импортируются ради тестов на согласованность словарей с кодами моделей.
__all__ = [
    "BADGE_CODES",
    "BADGE_LABELS",
    "GAME_CODES",
    "GAME_LABELS",
    "GAME_SHORT",
    "STATUS_LABELS",
    "TOURNAMENT_STATUSES",
    "badge_label",
    "game_label",
    "status_label",
]
