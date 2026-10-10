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
    # Student session lifetime in days (used as the signed cookie's max_age).
    STUDENT_SESSION_TTL_DAYS: int = 30
    # Default lifetime, in days, of a newly created invite code. The admin may
    # override it per code on the /admin/invites form.
    INVITE_TTL_DAYS: int = 14
    # Minimum number of days between two manual alias changes by the same
    # student. The cooldown protects against harassment and keeps a student
    # from "running away" from their reputation; the very first change (while
    # alias_changed_at is still NULL) is always allowed.
    ALIAS_CHANGE_COOLDOWN_DAYS: int = 30


settings = Settings()
