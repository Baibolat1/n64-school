"""Pagination helpers shared by the listing pages (feed, teacher reviews).

Listing pages are server-rendered, so paging is a query parameter and a link.
The two rules every caller needs are the same: junk in ``?page=`` means "first
page" rather than an error, and a page past the end means "last page" rather
than an empty screen.
"""

from math import ceil

__all__ = ["page_window", "parse_page"]


def parse_page(raw: str | None) -> int:
    """Coerce the ``page`` query parameter to a positive int (junk -> page 1).

    Negative, zero, empty and non-numeric values all mean "first page" instead
    of raising a validation error.
    """
    try:
        value = int(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 1
    return value if value >= 1 else 1


def page_window(page: int, total: int, page_size: int) -> tuple[int, int]:
    """Return ``(page, total_pages)`` with ``page`` pulled back into range.

    ``total_pages`` is never below 1: an empty listing is still "page 1 of 1",
    so the template does not have to special-case it.
    """
    total_pages = max(1, ceil(total / page_size))
    return min(page, total_pages), total_pages
