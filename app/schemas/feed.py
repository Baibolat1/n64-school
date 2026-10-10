"""Pydantic schemas for the feed forms (new post, new comment).

``body`` defaults to an empty string and ``is_anonymous`` to False so a missing
field (e.g. an unchecked checkbox, which browsers do not submit) is treated as
"empty body" / "not anonymous" rather than a validation error -- the service
then produces the friendly refusal instead of a 422.
"""

from pydantic import BaseModel


class PostCreate(BaseModel):
    """Fields of the "new post" form."""

    body: str = ""
    is_anonymous: bool = False


class CommentCreate(BaseModel):
    """Fields of the "new comment" form."""

    body: str = ""
    is_anonymous: bool = False
