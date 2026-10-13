"""Presentation helpers for the built-in avatar gallery, not business logic.

A student may pick a picture from a fixed set of SVGs that ship with the site;
there is no upload, so the database only ever stores one of :data:`AVATAR_KEYS`
and the file itself stays under version control. This module turns such a key
into a URL and decides whether a given record is allowed to show a picture at
all. Nothing here touches the database or the request lifecycle.
"""

from pathlib import Path

__all__ = [
    "ANONYMOUS_LABEL",
    "AVATAR_DIR",
    "AVATAR_KEYS",
    "AVATAR_LABELS",
    "AVATAR_URL_PREFIX",
    "avatar_image_key",
    "avatar_label",
    "avatar_options",
    "avatar_url",
    "is_valid_avatar_key",
]

# Where the gallery lives on disk. Used to check the keys against the files that
# are actually present (see tests/test_avatars.py), never to build a URL.
AVATAR_DIR = Path(__file__).resolve().parents[1] / "static" / "img" / "avatars"

# The gallery, in the order it is shown on /me. The name of each file without
# the extension is its key: circle.svg is the key "circle".
AVATAR_KEYS: tuple[str, ...] = (
    "circle",
    "ring",
    "waves",
    "peaks",
    "leaf",
    "moon",
    "star",
    "diamond",
    "grid",
    "spiral",
    "petals",
    "dawn",
)

# Human-readable names for the gallery, keyed by key and in the same order as
# AVATAR_KEYS. The labels exist so each radio option in the picker has an
# accessible name: the picture itself is marked ``aria-hidden`` (it is pure
# decoration, and would otherwise be read as a blank image), so without a label
# a screen reader could announce nothing but "radio button".
AVATAR_LABELS: dict[str, str] = {
    "circle": "Кружок",
    "ring": "Кольцо",
    "waves": "Волны",
    "peaks": "Горы",
    "leaf": "Лист",
    "moon": "Луна",
    "star": "Звезда",
    "diamond": "Ромб",
    "grid": "Сетка",
    "spiral": "Спираль",
    "petals": "Лепестки",
    "dawn": "Рассвет",
}

# Must stay equal to ANONYMOUS_ALIAS in app.services.publication. Importing that
# constant would drag the service layer into this presentation module, so the
# two are kept in sync by a test instead.
ANONYMOUS_LABEL = "Аноним"

# Every gallery image is served from here (see the /static mount in app.main).
AVATAR_URL_PREFIX = "/static/img/avatars"


def is_valid_avatar_key(key: str | None) -> bool:
    """Return whether ``key`` names a picture in the gallery.

    The check is strict membership, so a NULL (no picture chosen) and any other
    stray string both come back as invalid.
    """
    return key in AVATAR_KEYS


def avatar_url(key: str | None) -> str | None:
    """Return the URL of the gallery picture for ``key``, or ``None``.

    ``None`` covers both "no picture chosen" and a key that is not in the
    gallery. A URL is only ever built from one of :data:`AVATAR_KEYS`, so even
    if the database happens to hold garbage (an old value, a hand-edited row) it
    can never reach the URL: this rules out a broken link and blocks a crafted
    key such as ``../`` from escaping the avatars directory (path traversal).
    """
    if not is_valid_avatar_key(key):
        return None
    return f"{AVATAR_URL_PREFIX}/{key}.svg"


def avatar_image_key(pseudonym: str, key: str | None) -> str | None:
    """Return the picture key to show for a record, or ``None``.

    Anonymous content must never reveal a personal picture: the alias on it is
    the shared :data:`ANONYMOUS_LABEL`, so pairing that label with an avatar
    would single the author out among otherwise identical anonymous records and
    undo the anonymity. The guard therefore lives here, where every caller
    passes the alias the record was actually published under, rather than at
    each rendering site -- one forgotten caller would be enough to leak it.
    A non-anonymous record keeps its chosen picture, if any.
    """
    if pseudonym == ANONYMOUS_LABEL:
        return None
    return key if is_valid_avatar_key(key) else None


def avatar_options() -> tuple[str, ...]:
    """Return the gallery keys in display order, for the picker to loop over.

    Only the keys, not the URLs: the picture itself is drawn by the ``avatar``
    macro, which is the one place allowed to turn a key into a URL (and the one
    place that keeps anonymous records from showing a picture at all). Handing
    URLs out here would invite a template to skip the macro.
    """
    return AVATAR_KEYS


def avatar_label(key: str) -> str:
    """Return the human-readable name of a gallery picture.

    An unknown key falls back to the key itself rather than raising: a template
    that renders a stray value from an old row must still produce a page, and a
    key without a label would otherwise crash the picker. Never fails.
    """
    return AVATAR_LABELS.get(key, key)
