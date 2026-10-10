"""PII (personal-data) filter for user-generated content.

Pure, self-contained module: it depends only on the standard library
(:mod:`re`, :mod:`unicodedata`) and never touches the database, the app
settings or the web layer. The publishing flow wires it in a later stage; here
it is only the reusable text verdict.

Public API:

* :func:`normalize_text` -- canonicalize raw input before analysis so that
  obfuscation (zero-width characters, ``example @ mail.ru``, full-width or
  exotic whitespace) cannot slip a rule through.
* :func:`check_text` -- classify a whole text and return a frozen
  :class:`FilterResult`.

Every rule is described as data in :data:`RULES` (code, severity, compiled
pattern, human description). Adding or tuning a rule in a later stage means
editing that table, not the matching code.
"""

import re
import unicodedata
from dataclasses import dataclass
from enum import Enum

__all__ = [
    "PLACEHOLDER",
    "MAX_MATCH_FRAGMENT",
    "Rule",
    "Severity",
    "Verdict",
    "Match",
    "FilterResult",
    "RULES",
    "normalize_text",
    "check_text",
]

# Text substituted for a redacted fragment in ``FilterResult.clean_text``. One
# constant so the moderation UI and the student-facing preview stay in sync.
PLACEHOLDER = "[скрыто]"

# Upper bound on a fragment copied into ``FilterResult.matches``. The full text
# must never leave the analysis layer -- only short, truncated snippets do, so a
# moderation queue entry cannot become a second copy of the original message.
MAX_MATCH_FRAGMENT = 40


class Verdict(str, Enum):
    """Overall classification of a text."""

    PASS = "pass"
    FLAG = "flag"
    BLOCK = "block"


class Severity(str, Enum):
    """How strongly a rule condemns the text it matches."""

    HARD = "hard"  # any match -> BLOCK
    SOFT = "soft"  # match -> at most FLAG


@dataclass(frozen=True)
class Rule:
    """A single PII detection rule, described as data.

    ``pattern`` is matched against normalized text; ``severity`` decides whether
    a hit blocks the message or merely flags it for moderation. ``suppressed_by``
    lists codes of more specific rules: a hit fully contained in a match of one
    of them is dropped (e.g. a bare domain inside an e-mail address).
    """

    code: str
    severity: Severity
    pattern: re.Pattern
    description: str
    suppressed_by: tuple = ()


@dataclass(frozen=True)
class Match:
    """A fired rule and a short, truncated snippet of what it matched.

    Deliberately a fragment, never the matched region in full: the snippet is
    what a moderation queue can hold without duplicating the message.
    """

    code: str
    fragment: str


@dataclass(frozen=True)
class FilterResult:
    """Verdict for a single text.

    ``codes`` is the set of every rule that fired (hard and soft alike), so a
    caller can always see *why* -- e.g. a blocking verdict still lists the soft
    full-name hit alongside the phone hit. ``clean_text`` is the normalized text
    with every matched region replaced by :data:`PLACEHOLDER`; ``matches`` holds
    the truncated snippets.
    """

    verdict: Verdict
    codes: frozenset
    clean_text: str
    matches: tuple


# --- Normalization ------------------------------------------------------------

# Invisible / formatting characters that must not split a rule's pattern: a
# zero-width space inside "+7<ZWSP>912..." would otherwise defeat the phone
# regex. NFKC below does not remove them, so we strip them explicitly.
_INVISIBLE_RE = re.compile(
    "["
    "­"  # soft hyphen
    "؜"  # Arabic letter mark
    "᠎"  # Mongolian vowel separator
    "​-‏"  # zero-width space..right-to-left mark
    "‪-‮"  # bidi embedding/override
    "⁠-⁤"  # word joiner, invisible operators
    "⁦-⁯"  # bidi isolates, deprecated format chars
    "ㅤ"  # hangul filler
    "﻿"  # zero-width no-break space (BOM)
    "ﾠ"  # halfwidth hangul filler
    "]"
)

# Quote and dash variants folded to their ASCII counterparts so a rule sees one
# canonical form.
_TRANSLATION = str.maketrans(
    {
        "«": '"',
        "»": '"',
        "„": '"',
        "“": '"',
        "”": '"',
        "‟": '"',
        "‘": "'",
        "’": "'",
        "‚": "'",
        "‛": "'",
        "–": "-",
        "—": "-",
        "−": "-",
        "‒": "-",
        "―": "-",
        "‐": "-",
        "‑": "-",
    }
)

_WHITESPACE_RE = re.compile(r"\s+")


def normalize_text(raw: str) -> str:
    """Canonicalize ``raw`` before analysis and for ``clean_text``.

    Applies NFKC (composes, folds full-width forms and non-breaking spaces),
    removes invisible/formatting characters, unifies quotes and dashes, and
    collapses every run of whitespace (including newlines) to a single space.
    """
    text = unicodedata.normalize("NFKC", raw)
    text = _INVISIBLE_RE.sub("", text)
    text = text.translate(_TRANSLATION)
    return _WHITESPACE_RE.sub(" ", text)


# --- Rule patterns ------------------------------------------------------------
#
# Patterns are built from named fragments so the intent stays readable and the
# table below reads as data.

# Phone: a Russian number, either with a country prefix (+7 / 8) followed by ten
# digits, or a bare ten-digit subscriber number starting with 9 (the mobile
# range). Separators (space, hyphen, parentheses) may appear between digits. The
# digit lookarounds keep the match from starting inside a longer digit run, so a
# bare year or price never matches.
_PHONE_RE = re.compile(
    r"(?<!\d)(?:\+7|8)[\s\-()]*\d(?:[\s\-()]*\d){9}(?!\d)"
    r"|(?<!\d)\(?9\d{2}\)?[\s\-]*\d{3}[\s\-]*\d{2}[\s\-]*\d{2}(?!\d)"
)

# Email: local part, optional whitespace around "@", domain, and a letters-only
# TLD of two or more characters. The spaces around "@" catch "example @ mail.ru".
_EMAIL_RE = re.compile(
    r"[A-Za-z0-9._%+\-]+\s*@\s*"
    r"[A-Za-zА-Яа-яЁё0-9\-]+(?:\.[A-Za-zА-Яа-яЁё0-9\-]+)*\.[A-Za-zА-Яа-яЁё]{2,}"
)

# Link zones recognised on a "bare" domain (no scheme, no "www.").
_LINK_ZONES = "online|site|com|net|org|рф|io|me|tv|ru"

# Link: an explicit scheme or "www." prefix, or a bare domain ending in one of
# the common zones. The lookbehind stops a domain from being carved out of a
# longer token (a sub-label, an email address, a file name); messenger hosts are
# left to the dedicated rule so they are not reported twice.
_LINK_RE = re.compile(
    r"(?:https?://|www\.)[^\s]+"
    r"|(?<![\w@.\-])(?!(?:t\.me|vk\.com|wa\.me|discord\.gg)\b)"
    r"[A-Za-zА-Яа-яЁё0-9\-]+(?:\.[A-Za-zА-Яа-яЁё0-9\-]+)*"
    r"\.(?:" + _LINK_ZONES + r")\b(?:/[^\s]*)?",
    re.IGNORECASE,
)

# Messenger: a known host with an optional path, an "@handle" mention (not the
# local part of an email -- the lookbehind forbids a preceding word char, dot or
# "@"), or a messenger word directly tied to a Latin handle ("телеграм: vanya").
# A messenger word on its own ("Скинул в телеграм") matches nothing.
_MESSENGER_RE = re.compile(
    r"(?<![\w@.\-])(?:https?://)?(?:t\.me|vk\.com|wa\.me|discord\.gg)(?:/[^\s]*)?"
    r"|(?<![\w.@])@[A-Za-z0-9_]{4,}\b"
    r"|(?:телеграм\w*|телега|вотсап\w*|ватсап\w*|whatsapp\w*|telegram\w*)"
    r"[\s:=\-]+@?[A-Za-z][A-Za-z0-9_]{3,}\b",
    re.IGNORECASE,
)

# Capitalized Cyrillic words commonly opening a sentence that are *not* names.
# Excluded from the "plain word" position of the full-name heuristic so that
# "Он Иванов" (a lone surname after a pronoun) does not read as a full name.
_NAME_STOP = "|".join(
    (
        "Он", "Она", "Оно", "Они", "Это", "Мы", "Вы", "Ты",
        "Но", "А", "И", "В", "На", "У", "С", "К", "По", "За", "Из", "Не",
        "Как", "Что", "Где", "Когда", "Если", "Для", "При", "От", "Об", "О",
        "Мой", "Моя", "Моё", "Твой", "Наш", "Ваш", "Этот", "Эта", "Эти",
        "Тот", "Та", "Те", "Все", "Весь", "Всё", "Потом", "Тогда", "Ещё",
        "Только", "Даже", "Очень", "Тоже", "Также", "Мне", "Меня", "Нас",
        "Вас", "Его", "Её", "Их", "Им", "Пусть", "Давай",
    )
)

# A capitalized Cyrillic word that is not a common function word.
_NAME_WORD = r"(?!(?:" + _NAME_STOP + r")\b)[А-ЯЁ][а-яё]+"

# A capitalized word carrying a patronymic suffix or a surname ending. Longer
# alternatives come first so the greedy stem backtracks onto the longest match.
_SURNAME_WORD = (
    r"[А-ЯЁ][а-яё]*"
    r"(?:ович|евич|овна|евна|ична|ский|ская|енко|ина|ова|ева|ов|ев|ин|ук)\b"
)

# Full name: two or three consecutive capitalized words where one of them carries
# a patronymic/surname marker. A single capitalized word never matches.
_FULL_NAME_RE = re.compile(
    _SURNAME_WORD + r"\s+" + _NAME_WORD + r"(?:\s+" + _NAME_WORD + r")?"
    r"|" + _NAME_WORD + r"\s+" + _SURNAME_WORD + r"(?:\s+" + _NAME_WORD + r")?"
    r"|" + _NAME_WORD + r"\s+" + _NAME_WORD + r"\s+" + _SURNAME_WORD
)

# Address: a street word followed by a name, a house/apartment number, or a bare
# six-digit postal index.
_ADDRESS_RE = re.compile(
    r"\b(?:ул\.|улица|проспект|пр-т|просп\.|переулок|пер\.|бульвар|б-р|шоссе)"
    r"\s+[\w.\-]+"
    r"|\b(?:дом|д\.)\s*\d+"
    r"|\b(?:квартира|кв\.)\s*\d+"
    r"|(?<!\d)\d{6}(?!\d)",
    re.IGNORECASE,
)

# Class/group: a number with a lone class letter ("7А", "11 Б", "9-а"), a number
# tied to the words "класс"/"группа", or "группа 12". The trailing word boundary
# after the class letter is what keeps "5 раз" and "8 часов" from matching.
_CLASS_GROUP_RE = re.compile(
    r"\b\d{1,2}\s*-?\s*[А-ЯЁA-Zа-яёa-z]\b"
    r"|\b\d{1,2}\s*-?\s*(?:класс|групп)\w*"
    r"|\bгрупп\w*\s*\d{1,2}\b",
    re.IGNORECASE,
)


RULES = (
    Rule(
        "phone",
        Severity.HARD,
        _PHONE_RE,
        "Российский номер телефона: +7/8 и десять цифр, либо десять цифр "
        "мобильного диапазона, в том числе с разделителями.",
    ),
    Rule(
        "email",
        Severity.HARD,
        _EMAIL_RE,
        "Адрес электронной почты, в том числе с пробелами вокруг собаки.",
    ),
    Rule(
        "link",
        Severity.HARD,
        _LINK_RE,
        "Ссылка: схема http(s)://, префикс www. или домен в типичной зоне.",
        suppressed_by=("email",),
    ),
    Rule(
        "messenger",
        Severity.HARD,
        _MESSENGER_RE,
        "Мессенджер/соцсеть: известный хост, упоминание @ник или слово "
        "мессенджера в связке с ником.",
    ),
    Rule(
        "full_name",
        Severity.SOFT,
        _FULL_NAME_RE,
        "Эвристика ФИО: два-три слова с заглавной буквы с отчеством или "
        "фамильным окончанием.",
    ),
    Rule(
        "address",
        Severity.SOFT,
        _ADDRESS_RE,
        "Адрес: улица/проспект и т.п., дом/квартира с номером или "
        "шестизначный индекс.",
    ),
    Rule(
        "class_group",
        Severity.SOFT,
        _CLASS_GROUP_RE,
        "Номер класса или группы: 7А, 11 Б, 9-а, 3 класс, группа 12.",
    ),
)


# --- Matching -----------------------------------------------------------------


def _cluster(found):
    """Merge overlapping rule hits into non-overlapping clusters.

    ``found`` is an iterable of ``(start, end, rule)``. Overlapping ranges are
    unioned and their rules collected, so a region covered by several rules is
    replaced by a single placeholder and its codes are not lost. Ranges that only
    touch (``start == previous end``) stay separate.
    """
    ordered = sorted(found, key=lambda item: (item[0], item[1]))
    if not ordered:
        return []

    clusters = []
    start, end, rules = ordered[0][0], ordered[0][1], {ordered[0][2]}
    for next_start, next_end, rule in ordered[1:]:
        if next_start < end:
            end = max(end, next_end)
            rules.add(rule)
        else:
            clusters.append((start, end, rules))
            start, end, rules = next_start, next_end, {rule}
    clusters.append((start, end, rules))
    return clusters


def _fragment(text: str) -> str:
    """A snippet of ``text`` no longer than :data:`MAX_MATCH_FRAGMENT` chars."""
    if len(text) <= MAX_MATCH_FRAGMENT:
        return text
    return text[: MAX_MATCH_FRAGMENT - 1] + "…"


def check_text(raw: str) -> FilterResult:
    """Classify ``raw`` and return its :class:`FilterResult`.

    The text is normalized first. Every rule runs over it; overlapping hits are
    merged, then any HARD hit makes the verdict BLOCK, otherwise any hit at all
    makes it FLAG, otherwise PASS.
    """
    text = normalize_text(raw)

    found = []
    for rule in RULES:
        for match in rule.pattern.finditer(text):
            if match.start() == match.end():
                continue
            found.append((match.start(), match.end(), rule))

    # Drop hits swallowed by a more specific rule (a bare domain inside an
    # e-mail address, say) so only the precise code is reported.
    spans_by_code = {}
    for start, end, rule in found:
        spans_by_code.setdefault(rule.code, []).append((start, end))
    found = [
        hit
        for hit in found
        if not any(
            outer_start <= hit[0] and hit[1] <= outer_end
            for code in hit[2].suppressed_by
            for outer_start, outer_end in spans_by_code.get(code, ())
        )
    ]

    clusters = _cluster(found)

    verdict = Verdict.PASS
    codes = set()
    matches = []
    parts = []
    cursor = 0
    for start, end, rules in clusters:
        codes.update(rule.code for rule in rules)
        if any(rule.severity is Severity.HARD for rule in rules):
            verdict = Verdict.BLOCK
        elif verdict is not Verdict.BLOCK:
            verdict = Verdict.FLAG

        for code in sorted({rule.code for rule in rules}):
            matches.append(Match(code=code, fragment=_fragment(text[start:end])))

        parts.append(text[cursor:start])
        parts.append(PLACEHOLDER)
        cursor = end
    parts.append(text[cursor:])

    return FilterResult(
        verdict=verdict,
        codes=frozenset(codes),
        clean_text="".join(parts),
        matches=tuple(matches),
    )
