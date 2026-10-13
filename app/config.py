from pydantic_settings import BaseSettings, SettingsConfigDict


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
    SECRET_KEY: str = "change-me"
    DATABASE_URL: str = "sqlite:///./school.db"
    DEBUG: bool = False

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
