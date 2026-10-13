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

from app.core.deps import require_csrf, require_not_banned
from app.core.pagination import page_window, parse_page
from app.core.templates import templates
from app.db.base import get_db
from app.models.content import CONTENT_STATUS_VISIBLE, Post
from app.models.student import Student
from app.repositories import comments as comments_repo
from app.repositories import likes as likes_repo
from app.repositories import posts as posts_repo
from app.repositories import students as students_repo
from app.schemas.feed import CommentCreate, PostCreate
from app.services import feed as feed_service
from app.services import likes as likes_service
from app.services import school as school_service

# ``require_csrf`` is attached to the whole router: it is a no-op for safe
# methods, and every state-changing route here is a form POST.
router = APIRouter(tags=["feed"], dependencies=[Depends(require_csrf)])

# Posts per page; a fixed size keeps the pagination links simple.
PAGE_SIZE = 20


def _body_error_message(
    reason: feed_service.FeedError | None, max_length: int, retry_after: int = 0
) -> str:
    """Human-readable refusal message for a failed create attempt.

    The wording lives in the service, not here: the async endpoint refuses the
    same attempts and has to say the same thing (see app.services.feed).
    """
    return feed_service.refusal_message(reason, max_length, retry_after)


def _refusal_status(
    result: feed_service.PostCreateResult | feed_service.CommentCreateResult,
) -> int:
    """HTTP status for a refused create attempt.

    Rate limiting is the one refusal a client should be able to act on
    programmatically, so it gets its own status (and a ``Retry-After`` header);
    a content refusal stays a 200 with the form re-rendered.
    """
    if result.reason is feed_service.FeedError.RATE_LIMITED:
        return status.HTTP_429_TOO_MANY_REQUESTS
    return status.HTTP_200_OK


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


def _like_redirect_target(raw_next: str, post_id: int) -> str:
    """Where to send the browser back to after a like, without an open redirect.

    A like button lives both in the feed and on the post's own page, so the form
    carries ``next_url`` to say which one it was. Only the two exact URLs that
    can contain that button are accepted -- never a URL from the request in
    general, which would turn this endpoint into a redirector to any site.
    """
    allowed = {"/feed", f"/feed/posts/{post_id}"}
    return raw_next if raw_next in allowed else "/feed"


def _render_feed(
    request: Request,
    db: Session,
    student: Student,
    *,
    page: int,
    error: str | None = None,
    body: str = "",
    is_anonymous: bool = False,
    status_code: int = status.HTTP_200_OK,
) -> Response:
    total = posts_repo.count_visible_posts(db)
    page, total_pages = page_window(page, total, PAGE_SIZE)
    posts = posts_repo.list_visible_posts(
        db, limit=PAGE_SIZE, offset=(page - 1) * PAGE_SIZE
    )
    # The author's picture is always the current one, so it is fetched on every
    # render rather than snapshotted with the record (unlike ``display_alias``),
    # and in one query per page rather than one per card.
    avatar_keys = students_repo.avatar_keys_by_id(
        db, [post.author_id for post in posts]
    )
    # Likes are counted on every card, so both the counters and "which of these
    # has this student already liked" come back in a single query each (see
    # app.repositories.likes).
    post_ids = [post.id for post in posts]
    likes = likes_repo.count_likes_by_post(db, post_ids)
    liked_post_ids = likes_repo.liked_post_ids(db, student.id, post_ids)
    return templates.TemplateResponse(
        request,
        "feed.html",
        {
            "student": student,
            "posts": [(post, comments_repo.count_visible_comments(db, post.id))
                      for post in posts],
            "avatar_keys": avatar_keys,
            "like_counts": likes,
            "liked_post_ids": liked_post_ids,
            "page": page,
            "total_pages": total_pages,
            "error": error,
            # Echo the submitted text/checkbox back on error.
            "body": body,
            "is_anonymous": is_anonymous,
            # The school-day widget on top of the feed: today's lessons and the
            # nearest homework, or a dict that renders to nothing.
            "day": school_service.day_summary(db, student.class_name),
            "day_labels": school_service.DAY_LABELS,
        },
        status_code=status_code,
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
    status_code: int = status.HTTP_200_OK,
) -> Response:
    comments = comments_repo.list_visible_comments(db, post.id)
    # The same rule as the feed: the current picture of every author, in one
    # query for the post and all its comments together.
    avatar_keys = students_repo.avatar_keys_by_id(
        db, [post.author_id, *(comment.author_id for comment in comments)]
    )
    # Comments have no likes, so only the post itself is counted here.
    return templates.TemplateResponse(
        request,
        "post.html",
        {
            "student": student,
            "post": post,
            "comments": comments,
            "avatar_keys": avatar_keys,
            "like_counts": likes_repo.count_likes_by_post(db, [post.id]),
            "liked_post_ids": likes_repo.liked_post_ids(db, student.id, [post.id]),
            "error": error,
            "comment_body": comment_body,
            "comment_is_anonymous": comment_is_anonymous,
        },
        status_code=status_code,
    )


@router.get("/feed", response_class=HTMLResponse)
def feed(
    request: Request,
    page: str | None = None,
    student: Student = Depends(require_not_banned),
    db: Session = Depends(get_db),
) -> Response:
    return _render_feed(request, db, student, page=parse_page(page))


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
    response = _render_feed(
        request,
        db,
        student,
        page=1,
        error=_body_error_message(
            result.reason, feed_service.MAX_POST_LENGTH, result.retry_after
        ),
        body=result.body,
        is_anonymous=form.is_anonymous,
        status_code=_refusal_status(result),
    )
    if result.retry_after:
        response.headers["Retry-After"] = str(result.retry_after)
    return response


@router.get("/feed/posts/{post_id}", response_class=HTMLResponse)
def post_page(
    request: Request,
    post_id: int,
    student: Student = Depends(require_not_banned),
    db: Session = Depends(get_db),
) -> Response:
    post = _get_visible_post(db, post_id)
    return _render_post(request, db, student, post)


@router.post("/feed/posts/{post_id}/like", response_class=HTMLResponse)
def toggle_like(
    post_id: int,
    next_url: Annotated[str, Form()] = "",
    student: Student = Depends(require_not_banned),
    db: Session = Depends(get_db),
) -> Response:
    """Toggle the current student's like on a post and go straight back.

    Post/Redirect/Get like the other writes: the heart and the counter are part
    of the page, so the fresh render is what shows the new state. The result of
    the toggle is not read here -- the page can see the outcome for itself, and
    no message is needed either way.
    """
    post = _get_visible_post(db, post_id)
    likes_service.toggle_like(db, student, post)
    return RedirectResponse(
        url=_like_redirect_target(next_url, post_id),
        status_code=status.HTTP_303_SEE_OTHER,
    )


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
    response = _render_post(
        request,
        db,
        student,
        post,
        error=_body_error_message(
            result.reason, feed_service.MAX_COMMENT_LENGTH, result.retry_after
        ),
        comment_body=result.body,
        comment_is_anonymous=form.is_anonymous,
        status_code=_refusal_status(result),
    )
    if result.retry_after:
        response.headers["Retry-After"] = str(result.retry_after)
    return response
