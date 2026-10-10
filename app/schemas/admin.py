"""Pydantic schemas for admin authentication."""

from pydantic import BaseModel, ConfigDict


class AdminLogin(BaseModel):
    """Credentials submitted by the admin login form."""

    username: str
    password: str


class AdminOut(BaseModel):
    """Public view of an admin: no hashes or secrets."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
