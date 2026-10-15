from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# The repository root, used to resolve the paths that come from the environment
# (the database file, the teachers file). Resolving them against the project
# rather than the current directory is what makes the app indifferent to where
# it was started from -- a deployed process has no promise about its cwd.
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _absolute(path: str) -> str:
    """``path`` as an absolute path, unless it already is one or is ``:memory:``."""
    if path == ":memory:" or Path(path).is_absolute():
        return path
    return str(PROJECT_ROOT / path)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    APP_NAME: str = "Школа — закрытый сайт для учеников"
    # Signs the admin and student session cookies (itsdangerous) and keys the
    # HMAC used to hash invite codes. Rotating it invalidates every existing
    # student session and every previously issued invite code, so treat it as
    # permanent once the site is live.
    #
    # There is deliberately no default. A built-in placeholder would sign real
    # sessions on a deployed site with a key anybody can read in the source, and
    # the failure would be silent: everything keeps working, only the signatures
    # stop meaning anything. Empty is visible -- it warns at startup (see
    # app.core.logging) and, on Render, render.yaml generates the value.
    SECRET_KEY: str = ""
    # Where the SQLite file lives. The path is resolved against the repository
    # root, so the database is the same file no matter which directory the app
    # was started from. ``:memory:`` and absolute paths are taken as they are.
    DATABASE_PATH: str = "school.db"
    # Full SQLAlchemy URL, for anything that is not a local SQLite file (the
    # tests point this at their own temporary database). When it is set it wins
    # over DATABASE_PATH; see the ``database_url`` property below.
    DATABASE_URL: str = ""
    DEBUG: bool = False

    @property
    def database_url(self) -> str:
        """The SQLAlchemy URL the engine and alembic connect with."""
        if self.DATABASE_URL:
            return self.DATABASE_URL
        return f"sqlite:///{_absolute(self.DATABASE_PATH)}"

    # --- Demo data at startup -------------------------------------------------
    # Fill an empty database with the demonstration at startup. Meant for the
    # deployed demo, whose filesystem is wiped on every restart: the site would
    # otherwise come up as a login form and nothing else. The seeding is
    # idempotent and only runs when the database holds no students at all, so it
    # cannot touch a database that is already in use.
    SEED_DEMO: bool = False
    # Accounts for the jury of the hackathon, created by the startup seeding and
    # by ``scripts/seed_demo.py --with-jury``. All four are empty by default and
    # absent from the repository: no account is created unless they are set.
    JURY_LOGIN: str = ""
    JURY_PASSWORD: str = ""
    JURY_ADMIN_LOGIN: str = ""
    JURY_ADMIN_PASSWORD: str = ""

    # --- Admin authentication -------------------------------------------------
    # Name of the cookie that carries the raw admin session token.
    SESSION_COOKIE_NAME: str = "admin_session"
    # Admin session lifetime in hours (used to compute AdminSession.expires_at).
    ADMIN_SESSION_TTL_HOURS: int = 12
    # Send the session cookie only over HTTPS. Keep False for local development
    # over plain HTTP; set to True in production behind HTTPS.
    COOKIE_SECURE: bool = False
    # Credentials for the one-off seed script (scripts/seed_admin.py) only.
    # They are not used by the running application.
    ADMIN_USERNAME: str = ""
    ADMIN_PASSWORD: str = ""

    # --- Student authentication ----------------------------------------------
    # Name of the signed cookie that carries the student session.
    STUDENT_COOKIE_NAME: str = "student_session"
    # Name of the signed cookie that, during QR registration, carries the
    # novice's plaintext QR token so their own browser can re-render, refresh
    # and poll it. It is not a session and grants no access on its own.
    QR_COOKIE_NAME: str = "qr_registration"
    # Lifetime, in days, of the student session cookie *in the browser*, i.e.
    # how long the browser is told to store it. The signed token itself does
    # not expire: the student stays signed in until they log out. Browsers may
    # cap the stored lifetime by their own policy (Chrome and Firefox limit it
    # to roughly 400 days), in which case the student simply signs in again --
    # nothing breaks.
    STUDENT_SESSION_COOKIE_DAYS: int = 3650
    # Default lifetime, in days, of a newly created invite code. The admin may
    # override it per code on the /admin/invites form.
    INVITE_TTL_DAYS: int = 14
    # Upper bound, in days, of an invite code's lifetime: the admin may raise
    # the default, but not without limit -- a code that outlives its purpose is
    # a standing way in.
    INVITE_TTL_MAX_DAYS: int = 365
    # Minimum number of days between two manual alias changes by the same
    # student. The cooldown protects against harassment and keeps a student
    # from "running away" from their reputation; the very first change (while
    # alias_changed_at is still NULL) is always allowed.
    ALIAS_CHANGE_COOLDOWN_DAYS: int = 30

    # --- Student self-registration -------------------------------------------
    # How many students (in total, over all time -- invite, QR and
    # email/password alike) may exist before open email+password registration
    # closes. While ``COUNT(students) < OPEN_REGISTRATION_LIMIT`` anyone may
    # register with an email and a password; afterwards the only ways in are a
    # QR code confirmed by an existing student or an admin invite code.
    OPEN_REGISTRATION_LIMIT: int = 30
    # Minimum accepted password length for self-registered students.
    MIN_PASSWORD_LENGTH: int = 8
    # Upper bounds on the free-text fields the self-registration forms accept.
    # They keep a multi-megabyte body from reaching the regexes, the argon2 hash
    # or the database; they are also rendered as maxlength on the forms.
    # A long password is not a security win here (argon2 has no upper limit) but
    # an unbounded one is wasted work, hence the cap.
    MAX_EMAIL_LENGTH: int = 254
    MAX_PASSWORD_LENGTH: int = 128
    # Invite codes are much shorter than an email; the cap is checked before the
    # code is hashed.
    MAX_INVITE_CODE_INPUT_LENGTH: int = 32
    # Upper bound on the admin login field, rendered as maxlength there.
    MAX_ADMIN_USERNAME_LENGTH: int = 64
    # Lifetime, in hours, of a student registration QR token. After it expires
    # the novice must generate a new code ("Обновить код").
    QR_TOKEN_TTL_HOURS: int = 24
    # How many novices one student may confirm within ``QR_INVITE_WINDOW_HOURS``.
    # Confirming is an invitation: without a ceiling one account can walk in a
    # whole crowd. Counting is done on the confirmed tokens themselves (see
    # app.services.registration), so it survives a restart.
    QR_INVITES_PER_STUDENT: int = 3
    QR_INVITE_WINDOW_HOURS: int = 24
    # Total number of accounts that may ever be created through the QR flow. The
    # QR path is the way in *after* open registration closes, so this is the hard
    # ceiling on how large the site can grow on its own.
    QR_ACCOUNT_LIMIT: int = 40
    # Sign in with email and password (available to every student who has a
    # password, i.e. the first ``OPEN_REGISTRATION_LIMIT`` and QR-registered
    # ones) is throttled per client IP like the invite-code form.
    REGISTER_MAX_ATTEMPTS: int = 20
    REGISTER_WINDOW_SECONDS: int = 900

    # --- Rate limiting --------------------------------------------------------
    # Sliding-window budgets per student, counted in this process only (see
    # app.services.rate_limit and "Rate limit" in docs/PLAN.md). A limit of 0
    # switches the action off; windows are in seconds.
    RATE_LIMIT_POSTS: int = 10
    RATE_LIMIT_POSTS_WINDOW_SECONDS: int = 3600
    RATE_LIMIT_COMMENTS: int = 60
    RATE_LIMIT_COMMENTS_WINDOW_SECONDS: int = 3600
    # Reviews are a once-per-teacher affair rather than a stream, so the budget
    # only has to absorb edits, not a conversation.
    RATE_LIMIT_REVIEWS: int = 20
    RATE_LIMIT_REVIEWS_WINDOW_SECONDS: int = 3600
    # Questions to the assistant. A question that reaches the model costs real
    # money, so the budget is tighter than the posting one; answers served from
    # the school database are counted too, to keep the counter simple.
    RATE_LIMIT_ASSISTANT: int = 20
    RATE_LIMIT_ASSISTANT_WINDOW_SECONDS: int = 3600
    # Site-wide ceiling on questions per day, on top of the per-student window
    # above. A single student is bounded by their own limit; the whole audience
    # is not, and every question that reaches a model costs money. When the day's
    # budget is gone the assistant says so in words (see app.routers.school) --
    # the visitor has nothing to fix, so it is not an error page.
    ASSISTANT_DAILY_LIMIT: int = 200

    # --- Assistant (hybrid: school database first, model second) --------------
    # All three keys are optional and all empty by default: without a key the
    # assistant answers from the school database only and sends nothing
    # anywhere. The key is read here and never logged. When more than one is
    # set the first one wins, in the order they are declared: Anthropic, then
    # OpenAI, then DeepSeek.
    ANTHROPIC_API_KEY: str = ""
    # Model used for the free-form answers. Claude Opus 5.5 is the current
    # default; see docs/PLAN.md if it ever needs to change.
    ANTHROPIC_MODEL: str = "claude-opus-5-5"
    OPENAI_API_KEY: str = ""
    # Read from the account, not guessed: OpenAI hands out model ids that come
    # and go, so this is a starting point to be changed in .env rather than a
    # promise about what exists.
    OPENAI_MODEL: str = "gpt-4o-mini"
    DEEPSEEK_API_KEY: str = ""
    DEEPSEEK_MODEL: str = "deepseek-chat"
    # Root of each provider's API, and nothing more: app.services.assistant adds
    # the path itself (``/messages`` for Anthropic, ``/chat/completions`` for the
    # two OpenAI-compatible ones), so a trailing slash here changes nothing.
    # These exist for the cases the three documented addresses do not cover: a
    # proxy in front of a provider, a self-hosted gateway, or a local model.
    # DeepSeek is reachable both as ``https://api.deepseek.com`` and as
    # ``https://api.deepseek.com/v1`` -- two spellings of one endpoint, and the
    # first is what DeepSeek documents as its base URL.
    ANTHROPIC_BASE_URL: str = "https://api.anthropic.com/v1"
    OPENAI_BASE_URL: str = "https://api.openai.com/v1"
    DEEPSEEK_BASE_URL: str = "https://api.deepseek.com"
    # Upper bound on a generated answer, and how long to wait for it before
    # falling back to the school-database refusal text.
    ASSISTANT_MAX_TOKENS: int = 1024
    ASSISTANT_TIMEOUT_SECONDS: float = 30.0

    # Login attempts allowed per client IP before further attempts are refused
    # with 429 (see app.services.login_throttle). The admin form is strict: it
    # guards the moderation tools. The student form is soft: a mistyped invite
    # code is harmless, while a guessing loop is not. A successful login clears
    # the counter. The client IP is used only as an in-memory counter key -- it
    # is never stored in the database nor written to a log -- and behind a proxy
    # it is the proxy's address, so the limit is best read as "per connection".
    ADMIN_LOGIN_MAX_ATTEMPTS: int = 5
    ADMIN_LOGIN_WINDOW_SECONDS: int = 900
    STUDENT_LOGIN_MAX_ATTEMPTS: int = 20
    STUDENT_LOGIN_WINDOW_SECONDS: int = 900

    # --- Teacher reviews ------------------------------------------------------
    # Where the seeding script (scripts/seed_teachers.py) reads the list of
    # teachers and review criteria from when no --file is given. The default
    # path is deliberately one that is *not* in the repository (see .gitignore):
    # the real names and criteria are personal data of the school's staff and
    # are supplied per installation. The script falls back to the committed demo
    # file with invented names when this one does not exist, so nothing here is
    # required for the site to run.
    TEACHERS_DATA_FILE: str = "data/teachers.local.json"

    # --- CSRF -----------------------------------------------------------------
    # Name of the cookie that carries the signed CSRF token, and of the hidden
    # form field that must echo it. The cookie is httpOnly: the server renders
    # the value into the form, so no script needs to read it.
    CSRF_COOKIE_NAME: str = "csrf_token"
    CSRF_FORM_FIELD: str = "csrf_token"


settings = Settings()
