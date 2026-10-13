"""Shared Jinja2 template environment for server-rendered HTML pages."""

from pathlib import Path

from fastapi.templating import Jinja2Templates

from app.config import settings
from app.core.avatars import (
    avatar_image_key,
    avatar_label,
    avatar_options,
    avatar_url,
)
from app.core.csrf import csrf_context
from app.core.ui import avatar_index, avatar_initial

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"

# ``csrf_context`` injects ``csrf_token`` into every template, so a form only
# has to render ``{{ csrf_token }}`` in a hidden field -- see app.core.csrf.
templates = Jinja2Templates(
    directory=str(TEMPLATES_DIR), context_processors=[csrf_context]
)

# Avatar cosmetics are globals: templates call ``avatar_index`` and
# ``avatar_initial`` directly -- see app.core.ui.
templates.env.globals.update(
    avatar_index=avatar_index, avatar_initial=avatar_initial
)

# The built-in gallery is a global as well, so the avatar macro can turn a stored
# key into a picture without a caller knowing the /static layout -- see
# app.core.avatars.
templates.env.globals.update(
    avatar_url=avatar_url,
    avatar_image_key=avatar_image_key,
    avatar_options=avatar_options,
    avatar_label=avatar_label,
)

# The input caps are globals too, so a form can render its own ``maxlength``
# without the router having to reach into settings and pass them in context.
templates.env.globals.update(
    max_email_length=settings.MAX_EMAIL_LENGTH,
    max_password_length=settings.MAX_PASSWORD_LENGTH,
    max_invite_code_length=settings.MAX_INVITE_CODE_INPUT_LENGTH,
    max_admin_username_length=settings.MAX_ADMIN_USERNAME_LENGTH,
    invite_ttl_max_days=settings.INVITE_TTL_MAX_DAYS,
)
