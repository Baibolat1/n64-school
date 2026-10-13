"""Project logging: one logger, and only safe events on it.

Rule
----
Nothing that identifies a person ever reaches a log line. Bodies of posts and
comments, aliases, emails, IP addresses and invite or QR tokens are all out. A
log entry names an *event* and carries only technical fields -- HTTP method,
path without its query string, rate-limit scope, content type, rule codes.
Whoever reads a log file learns what happened, never who did it or what was
said.
"""

import logging

from app.config import settings

__all__ = ["logger", "configure_logging", "log_startup_warnings"]

# The single logger the whole application logs through. A name of its own
# ("app") keeps the project's lines apart from the framework's in a shared
# stream, so the level of one can be changed without touching the other.
logger = logging.getLogger("app")

_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"


def configure_logging() -> None:
    """Give the project logger a format and a level, once, at startup.

    ``force`` is deliberately left off: tearing the root handlers down would
    also strip the ones pytest installs for ``caplog`` and make captured
    warnings vanish. A handler is added only when the logger has none of its
    own, so importing the application (as the tests do) does not pile up
    duplicate output.
    """
    logger.setLevel(logging.DEBUG if settings.DEBUG else logging.INFO)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(_LOG_FORMAT))
        logger.addHandler(handler)


def log_startup_warnings() -> None:
    """Warn about an unsafe configuration; never stop the application.

    The site must still come up on a laptop exactly as configured, so each of
    these is a warning rather than an error: the process starts either way and
    the log says plainly why the current settings are a problem.
    """
    if not settings.DEBUG and not settings.COOKIE_SECURE:
        logger.warning(
            "COOKIE_SECURE выключен вне режима DEBUG: сессионная cookie может "
            "уйти по открытому HTTP. За HTTPS задайте COOKIE_SECURE=true, для "
            "локальной разработки по HTTP — DEBUG=true."
        )
    # The placeholder from ``.env.example`` counts as "not set": the README tells
    # you to copy that file, so the value actually in a fresh ``.env`` must trip
    # the warning as well.
    if not settings.SECRET_KEY or settings.SECRET_KEY.startswith("change-me"):
        logger.warning(
            "SECRET_KEY оставлен значением по умолчанию: подписывать сессии и "
            "коды приглашений дефолтным ключом нельзя, задайте SECRET_KEY в "
            "окружении."
        )
