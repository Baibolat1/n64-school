"""Saving the picture a student picks from the built-in avatar gallery.

Unlike an alias there is no cooldown: a student may set, change or drop the
picture as often as they like. The only rule is that the stored key must name
one of the gallery files in :data:`app.core.avatars.AVATAR_KEYS`, so nothing
but a whitelisted key can ever reach the URL -- no ``../`` and no stray path.
There is no upload and, as with aliases and posts, no word list.

Transaction policy (mirrors ``app.services.aliases``): this service layer owns
commits for write operations -- :func:`set_avatar` commits.
"""

from dataclasses import dataclass
from enum import Enum

from sqlalchemy.orm import Session

from app.core.avatars import is_valid_avatar_key
from app.models.student import Student


class AvatarError(str, Enum):
    """Machine-readable reason a picture change can be refused."""

    INVALID_KEY = "invalid_key"


@dataclass(frozen=True)
class AvatarChangeResult:
    """Outcome of a picture change attempt.

    ``key`` is what ended up stored -- a gallery key, or ``None`` when the
    picture was cleared -- and ``reason`` is set when ``ok`` is False.
    """

    ok: bool
    key: str | None = None
    reason: AvatarError | None = None


def set_avatar(
    db: Session, student: Student, raw_key: str
) -> AvatarChangeResult:
    """Store, clear or reject the picture ``student`` submitted.

    The value is trimmed first: it arrives from a radio field in the form, and a
    hand-crafted request with stray spaces must neither sneak past the whitelist
    nor blow up. An empty value (whitespace only included) clears the picture;
    any other value must be one of the gallery keys, otherwise nothing is
    changed and nothing is committed.

    There is no cooldown here (unlike the alias, see ``app.services.aliases``).
    The commit belongs to this service; the routers do not commit. It is
    unconditional -- including a clear when there is nothing to clear -- so the
    service always leaves the session with nothing pending, whatever a caller
    may have done to ``student`` before calling.
    """
    key = raw_key.strip()
    if not key:
        student.avatar_key = None
        db.commit()
        return AvatarChangeResult(ok=True, key=None)
    if not is_valid_avatar_key(key):
        return AvatarChangeResult(ok=False, reason=AvatarError.INVALID_KEY)
    student.avatar_key = key
    db.commit()
    return AvatarChangeResult(ok=True, key=key)
