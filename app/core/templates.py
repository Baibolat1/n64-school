"""Shared Jinja2 template environment for server-rendered HTML pages."""

from pathlib import Path

from fastapi.templating import Jinja2Templates

from app.core.csrf import csrf_context

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"

# ``csrf_context`` injects ``csrf_token`` into every template, so a form only
# has to render ``{{ csrf_token }}`` in a hidden field -- see app.core.csrf.
templates = Jinja2Templates(
    directory=str(TEMPLATES_DIR), context_processors=[csrf_context]
)
