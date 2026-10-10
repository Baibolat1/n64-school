"""Student alias generation and manual alias changes.

``generate_alias`` auto-produces a human-readable, unique alias at registration
time. The rest of this module implements the student-facing "choose my own
alias" flow: normalization, validation (length, character set, reserved service
names, a placeholder profanity filter), availability and the change cooldown.

Transaction policy (mirrors ``app.services.admin_auth``): this service layer
owns commits for write operations -- :func:`set_alias` commits.
"""

import re
import secrets
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.models.student import Student

# Adjective + noun + number gives a readable alias such as "Смелый-Кот-42".
_ADJECTIVES = (
    "Смелый",
    "Быстрый",
    "Тихий",
    "Ясный",
    "Ловкий",
    "Добрый",
    "Храбрый",
    "Умный",
    "Весёлый",
    "Гордый",
    "Зоркий",
    "Резвый",
    "Светлый",
    "Тёплый",
    "Чуткий",
    "Яркий",
    "Лёгкий",
    "Мудрый",
    "Стойкий",
    "Вольный",
)

_NOUNS = (
    "Кот",
    "Волк",
    "Сокол",
    "Лис",
    "Барс",
    "Орёл",
    "Тигр",
    "Ворон",
    "Медведь",
    "Ёж",
    "Филин",
    "Кит",
    "Дельфин",
    "Лебедь",
    "Скворец",
    "Рысь",
    "Выдра",
    "Морж",
    "Гриф",
    "Ястреб",
)

# Number of readable-alias attempts before falling back to a random suffix.
MAX_ATTEMPTS = 25

# Number of fallback attempts with a high-entropy suffix.
FALLBACK_ATTEMPTS = 25

# --- Manual alias rules -------------------------------------------------------

MIN_ALIAS_LENGTH = 3
MAX_ALIAS_LENGTH = 24

# Russian and Latin letters, digits, space, hyphen and underscore. Anything else
# (punctuation, emoji, URL separators, ...) is rejected with INVALID_CHARS.
_ALIAS_ALLOWED_RE = re.compile(r"^[A-Za-zА-Яа-яЁё0-9 _-]+$")

# Names that impersonate a role, a system account or anonymity. Matched
# case-insensitively after normalization, with optional trailing digits
# ("Аноним", "Anon007", ...).
_RESERVED_WORDS = (
    "аноним",
    "анонимный",
    "админ",
    "администратор",
    "модератор",
    "учитель",
    "ученик",
    "система",
    "гость",
    "anon",
    "admin",
    "moderator",
    "teacher",
    "system",
    "guest",
)
_RESERVED_RE = re.compile(
    r"^(?:"
    + "|".join(
        re.escape(word)
        for word in sorted(_RESERVED_WORDS, key=len, reverse=True)
    )
    + r")\d*$"
)

# Prefix of the fallback service-generated alias ("Ученик-AB12CD34"). The format
# is reserved so a student cannot masquerade as a service account.
_SERVICE_ALIAS_PREFIX = "ученик-"

# TEMPORARY STUB: a short list of profanity/insult roots. A real content filter
# arrives in a later stage; this placeholder is intentionally small and is no
# substitute for actual moderation.
_PROFANITY_ROOTS = (
    "хуй",
    "хуе",
    "пизд",
    "бляд",
    "блят",
    "ебан",
    "ебал",
    "ёбан",
    "ёбал",
    "мудак",
    "мудил",
    "гандон",
    "залуп",
    "долбо",
    "шлюх",
    "fuck",
    "shit",
)


class AliasError(str, Enum):
    """Machine-readable reasons a manual alias change can be refused."""

    TOO_SHORT = "too_short"
    TOO_LONG = "too_long"
    INVALID_CHARS = "invalid_chars"
    LEADING_DIGIT = "leading_digit"
    RESERVED = "reserved"
    FORBIDDEN = "forbidden"
    TAKEN = "taken"
    COOLDOWN = "cooldown"


@dataclass(frozen=True)
class AliasChangeResult:
    """Outcome of a manual alias change attempt.

    ``alias`` is the normalized submitted value (so the form can echo it back),
    ``reason`` is set when ``ok`` is False, and ``retry_at`` is set together with
    ``COOLDOWN`` to say when the next change becomes possible.
    """

    ok: bool
    alias: str
    reason: AliasError | None = None
    retry_at: datetime | None = None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    """Normalize a datetime read from SQLite (stored naive-UTC) to aware UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _fold(alias: str) -> str:
    """Case-insensitive comparison key for an alias."""
    return normalize_alias(alias).casefold()


def normalize_alias(raw: str) -> str:
    """Canonicalize a user-entered alias.

    Trims the ends, collapses runs of whitespace to a single space, and applies
    Unicode NFC normalization so visually identical input compares equal.
    """
    return unicodedata.normalize("NFC", " ".join(raw.split()))


def validate_alias(alias: str) -> AliasError | None:
    """Return the rejection reason for ``alias``, or None if it is acceptable.

    The input is normalized first, so callers may pass raw user text.
    """
    alias = normalize_alias(alias)
    if len(alias) < MIN_ALIAS_LENGTH:
        return AliasError.TOO_SHORT
    if len(alias) > MAX_ALIAS_LENGTH:
        return AliasError.TOO_LONG
    if not _ALIAS_ALLOWED_RE.match(alias):
        return AliasError.INVALID_CHARS
    if alias[0].isdigit():
        return AliasError.LEADING_DIGIT

    folded = alias.casefold()
    if _RESERVED_RE.match(folded) or folded.startswith(_SERVICE_ALIAS_PREFIX):
        return AliasError.RESERVED

    # Check profanity on a separator-free copy so "х-у-й" cannot slip through.
    compact = folded.replace(" ", "").replace("-", "").replace("_", "")
    if any(root in compact for root in _PROFANITY_ROOTS):
        return AliasError.FORBIDDEN

    return None


def _alias_taken(db: Session, alias: str) -> bool:
    stmt = select(Student.id).where(Student.alias == alias).limit(1)
    return db.execute(stmt).first() is not None


def is_alias_taken(db: Session, alias: str) -> bool:
    """Whether ``alias`` is already used, compared case-insensitively.

    SQLite's ``lower()``/``LIKE`` only fold ASCII, so Cyrillic aliases cannot be
    compared case-insensitively in SQL; the comparison is done in Python over
    the alias column (fine for a closed school site).
    """
    target = _fold(alias)
    for existing in db.execute(select(Student.alias)).scalars():
        if _fold(existing) == target:
            return True
    return False


def can_change_alias(student: Student) -> tuple[bool, datetime | None]:
    """Return ``(allowed, retry_at)`` for a manual change by ``student``.

    The first change while ``alias_changed_at`` is NULL is always allowed (no
    cooldown); afterwards the cooldown is measured from the last manual change.
    """
    if student.alias_changed_at is None:
        return True, None
    retry_at = _as_utc(student.alias_changed_at) + timedelta(
        days=settings.ALIAS_CHANGE_COOLDOWN_DAYS
    )
    if _utcnow() >= retry_at:
        return True, None
    return False, retry_at


def set_alias(db: Session, student: Student, raw_alias: str) -> AliasChangeResult:
    """Validate and persist a manual alias change for ``student``.

    On success the new alias and ``alias_changed_at`` are stored and committed.

    Race safety: a unique constraint on ``students.alias`` backs the pre-check,
    so two concurrent requests for the same alias need no locking -- the loser's
    commit raises ``IntegrityError`` and is reported as "taken", never a 500.

    Note for a later stage: once posts/comments exist they will store a
    ``display_alias`` snapshot taken at publish time, so changing an alias will
    not rewrite what was already published.
    """
    alias = normalize_alias(raw_alias)
    reason = validate_alias(alias)
    if reason is not None:
        return AliasChangeResult(ok=False, alias=alias, reason=reason)

    allowed, retry_at = can_change_alias(student)
    if not allowed:
        return AliasChangeResult(
            ok=False, alias=alias, reason=AliasError.COOLDOWN, retry_at=retry_at
        )

    if is_alias_taken(db, alias):
        return AliasChangeResult(ok=False, alias=alias, reason=AliasError.TAKEN)

    student.alias = alias
    student.alias_changed_at = _utcnow()
    try:
        db.commit()
    except IntegrityError:
        # Another request claimed the alias between our check and the commit.
        db.rollback()
        return AliasChangeResult(ok=False, alias=alias, reason=AliasError.TAKEN)

    return AliasChangeResult(ok=True, alias=alias)


def generate_alias(db: Session) -> str:
    """Return a unique, human-readable alias for a new student.

    The caller is responsible for persisting it; uniqueness is checked against
    ``students.alias`` within the current transaction.
    """
    for _ in range(MAX_ATTEMPTS):
        alias = (
            f"{secrets.choice(_ADJECTIVES)}-{secrets.choice(_NOUNS)}-"
            f"{secrets.randbelow(90) + 10}"
        )
        if not _alias_taken(db, alias):
            return alias

    # Practically never reached: fall back to a high-entropy suffix so that a
    # unique alias is always produced.
    for _ in range(FALLBACK_ATTEMPTS):
        alias = f"Ученик-{secrets.token_hex(4).upper()}"
        if not _alias_taken(db, alias):
            return alias

    raise RuntimeError("Не удалось сгенерировать уникальный псевдоним")
