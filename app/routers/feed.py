"""Feed routes: the post/comment pages for logged-in, non-banned students.

Every route requires a valid student session and a non-banned student, both
enforced by ``require_not_banned`` (anonymous visitors are redirected to
``/login``; banned students get the 403 page). The author of a post or comment
is taken from the session, never from the form or the URL.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.core.deps import require_not_banned
from app.core.templates import templates
from app.db.base import get_db
from app.models.content import CONTENT_STATUS_VISIBLE, Post
from app.models.student import Student
from app.repositories import comments as comments_repo
from app.repositories import posts as posts_repo
from app.schemas.feed import CommentCreate, PostCreate
from app.services import feed as feed_service

router = APIRouter(tags=["feed"])

# Posts per page; a fixed size keeps the pagination links simple.
PAGE_SIZE = 20


def _parse_page(raw: str | None) -> int:
    """Coerce the ``page`` query parameter to a positive int (junk -> page 1).

    Negative, zero, empty and non-numeric values all mean "first page" instead
    of raising a validation error.
    """
    try:
        value = int(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 1
    return value if value >= 1 else 1


def _body_error_message(reason: feed_service.FeedError | None, max_length: int) -> str:
    """Human-readable refusal message for a failed create attempt."""
    if reason is feed_service.FeedError.EMPTY:
        return "Текст не может быть пустым"
    if reason is feed_service.FeedError.TOO_LONG:
        return f"Слишком длинно: максимум {max_length} символов"
    return "Не удалось сохранить"


def _get_visible_post(db: Session, post_id: int) -> Post:
    """Load a visible post or raise 404.

    404 (not 403) for a hidden post so its existence is not revealed.
    """
    post = posts_repo.get_post(db, post_id)
    if post is None or post.status != CONTENT_STATUS_VISIBLE:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Пост не найден"
        )
    return post


def _render_feed(
    request: Request,
    db: Session,
    student: Student,
    *,
    page: int,
    error: str | None = None,
    body: str = "",
    is_anonymous: bool = False,
) -> Response:
    total = posts_repo.count_visible_posts(db)
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = min(page, total_pages)
    posts = posts_repo.list_visible_posts(
        db, limit=PAGE_SIZE, offset=(page - 1) * PAGE_SIZE
    )
    return templates.TemplateResponse(
        request,
        "feed.html",
        {
            "student": student,
            "posts": [(post, comments_repo.count_visible_comments(db, post.id))
                      for post in posts],
            "page": page,
            "total_pages": total_pages,
            "error": error,
            # Echo the submitted text/checkbox back on error.
            "body": body,
            "is_anonymous": is_anonymous,
        },
    )


def _render_post(
    request: Request,
    db: Session,
    student: Student,
    post: Post,
    *,
    error: str | None = None,
    comment_body: str = "",
    comment_is_anonymous: bool = False,
) -> Response:
    comments = comments_repo.list_visible_comments(db, post.id)
    return templates.TemplateResponse(
        request,
        "post.html",
        {
            "student": student,
            "post": post,
            "comments": comments,
            "error": error,
            "comment_body": comment_body,
            "comment_is_anonymous": comment_is_anonymous,
        },
    )


@router.get("/feed", response_class=HTMLResponse)
def feed(
    request: Request,
    page: str | None = None,
    student: Student = Depends(require_not_banned),
    db: Session = Depends(get_db),
) -> Response:
    return _render_feed(request, db, student, page=_parse_page(page))


@router.post("/feed/posts", response_class=HTMLResponse)
def create_post(
    request: Request,
    form: Annotated[PostCreate, Form()],
    student: Student = Depends(require_not_banned),
    db: Session = Depends(get_db),
) -> Response:
    result = feed_service.create_post(db, student, form.body, form.is_anonymous)
    if result.ok:
        # Post/Redirect/Get: a reload will not resubmit the form.
        return RedirectResponse(url="/feed", status_code=status.HTTP_303_SEE_OTHER)
    return _render_feed(
        request,
        db,
        student,
        page=1,
        error=_body_error_message(result.reason, feed_service.MAX_POST_LENGTH),
        body=result.body,
        is_anonymous=form.is_anonymous,
    )


@router.get("/feed/posts/{post_id}", response_class=HTMLResponse)
def post_page(
    request: Request,
    post_id: int,
    student: Student = Depends(require_not_banned),
    db: Session = Depends(get_db),
) -> Response:
    post = _get_visible_post(db, post_id)
    return _render_post(request, db, student, post)


@router.post("/feed/posts/{post_id}/comments", response_class=HTMLResponse)
def create_comment(
    request: Request,
    post_id: int,
    form: Annotated[CommentCreate, Form()],
    student: Student = Depends(require_not_banned),
    db: Session = Depends(get_db),
) -> Response:
    post = _get_visible_post(db, post_id)
    result = feed_service.create_comment(
        db, student, post, form.body, form.is_anonymous
    )
    if result.ok:
        return RedirectResponse(
            url=f"/feed/posts/{post.id}", status_code=status.HTTP_303_SEE_OTHER
        )
    return _render_post(
        request,
        db,
        student,
        post,
        error=_body_error_message(result.reason, feed_service.MAX_COMMENT_LENGTH),
        comment_body=result.body,
        comment_is_anonymous=form.is_anonymous,
    )
