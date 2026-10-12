"""Presentation helpers for view cosmetics (avatars), not business logic.

The avatar circle next to a pseudonym is drawn entirely in CSS: the palette
lives in ``--avatar-0`` .. ``--avatar-5`` (see app/static/css/theme.css) and a
template only sets ``data-color="{{ avatar_index(alias) }}"``. Picking that
index from the pseudonym cannot be expressed in plain CSS, hence this tiny
module. Nothing here touches the database or the request lifecycle, and the
same pseudonym always maps to the same colour on every page.
"""

import zlib

__all__ = ["AVATAR_COLORS", "avatar_index", "avatar_initial"]

# Number of tones in the CSS palette (--avatar-0 .. --avatar-5 in theme.css).
AVATAR_COLORS = 6

# Shown when a pseudonym has no letter or digit to take an initial from.
_FALLBACK_INITIAL = "?"


def avatar_index(pseudonym: str) -> int:
    """Return a stable palette index in ``0 .. AVATAR_COLORS - 1``.

    The pseudonym is hashed exactly as given (no case-folding or trimming), so
    two different pseudonyms get independent colours while the same one is
    always drawn the same way. An empty string is fine too: crc32 is defined
    for it, so the result stays deterministic rather than raising.
    """
    return zlib.crc32(pseudonym.encode("utf-8")) % AVATAR_COLORS


def avatar_initial(pseudonym: str) -> str:
    """Return the single letter shown inside the avatar circle.

    Uses the first alphanumeric character (``str.isalnum`` covers Cyrillic and
    other scripts; spaces, emoji and punctuation are skipped), upper-cased. If
    the pseudonym has no such character -- empty, only spaces, only emoji -- a
    fallback glyph is returned instead of failing.
    """
    for char in pseudonym:
        if char.isalnum():
            # Upper-casing is not always one-to-one (the German sharp s
            # expands to "SS"), so keep the first character: the circle holds
            # exactly one glyph.
            return char.upper()[:1]
    return _FALLBACK_INITIAL
