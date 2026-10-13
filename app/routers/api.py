"""JSON endpoints: everything the pages do without reloading.

The HTML routes stay exactly as they were -- every form on the site still posts
to a real URL and comes back with a real page. This router is the second way in,
and it exists so that a browser with JavaScript can do the same thing and stay
where it is: a like that fills its heart in place, a post that appears at the
top of the feed, a comment that lands under the one above it, an avatar that is
saved with a toast instead of a page turn.

Two rules hold across the whole file.

**Nothing new is decided here.** Every endpoint calls the same service the form
calls, so the PII filter, the rate limit, the length caps and the visibility
checks are the same ones, and a refusal is worded by the service rather than
re-invented (see ``app.services.feed.refusal_message``). The only thing this
layer adds is the shape of the answer.

**The body is form-encoded, not JSON.** That is what lets ``require_csrf`` be
reused untouched: it reads the token out of a parsed form body, and a JSON
endpoint would need a CSRF story of its own. The token comes from the hidden
field every form already renders, so the script has nothing to look up.

Fragments are HTML, not JSON objects. A created post is returned as the same
``_post_card.html`` the page renders, so the card in the feed and the card the
script inserted are one template and cannot drift apart.
"""

from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import JSONResponse, Response
from sqlalchemy.orm import Session

from app.config import settings
from app.core import markdown
from app.core.deps import require_csrf, require_not_banned
from app.core.templates import templates
from app.db.base import get_db
from app.models.content import CONTENT_STATUS_VISIBLE, Post
from app.models.student import Student
from app.repositories import comments as comments_repo
from app.repositories import likes as likes_repo
from app.repositories import posts as posts_repo
from app.repositories import students as students_repo
from app.services import aliases as aliases_service
from app.services import assistant as assistant_service
from app.services import avatars as avatars_service
from app.services import feed as feed_service
from app.services import likes as likes_service
from app.services import team as team_service
from app.services.rate_limit import limiter

router = APIRouter(prefix="/api", tags=["api"], dependencies=[Depends(require_csrf)])


def _ok(**payload) -> JSONResponse:
    return JSONResponse({"ok": True, **payload})


def _no(error: str, status_code: int = status.HTTP_200_OK, **payload) -> JSONResponse:
    """A refusal the script can show as-is.

    Content refusals stay 200, exactly as they do on the HTML routes: nothing
    about the request was malformed, the text simply cannot be published, and
    4xx would make a browser console treat a normal "слишком длинно" as an
    error. Rate limiting keeps its 429, the one refusal a client can act on.
    """
    return JSONResponse(
        {"ok": False, "error": error, **payload}, status_code=status_code
    )


def _fragment(request: Request, name: str, **context) -> str:
    """Render a partial to HTML for the script to insert.

    ``csrf_token`` is passed by hand because a context processor only runs for a
    full template response, and the partials carry forms of their own (the like
    button, the complaint link). No caller may render a partial with anything
    but the token the page itself was rendered with -- which is exactly what
    this does.
    """
    template = templates.env.get_template(name)
    return template.render(
        csrf_token=getattr(request.state, "csrf_token", ""), **context
    )


def _visible_post(db: Session, post_id: int) -> Post | None:
    """A visible post, or None.

    Not the HTML router's ``_get_visible_post``: that one raises an HTTPException
    so the 404 page renders, and a script cannot read a page. Here the caller
    turns None into a JSON 404 with the same wording.
    """
    post = posts_repo.get_post(db, post_id)
    if post is None or post.status != CONTENT_STATUS_VISIBLE:
        return None
    return post


@router.post("/posts/{post_id}/like")
def toggle_like(
    post_id: int,
    student: Student = Depends(require_not_banned),
    db: Session = Depends(get_db),
) -> Response:
    """Toggle the like and answer with the state it left.

    The client flips the heart and the counter before this returns -- that is
    what "instant" means on a tap -- so the numbers here are what it reconciles
    against. The state is recomputed from the session, never taken from the
    body: nobody can dictate a target through the request.
    """
    post = _visible_post(db, post_id)
    if post is None:
        return _no("Пост не найден", status.HTTP_404_NOT_FOUND)

    liked = likes_service.toggle_like(db, student, post)
    count = likes_repo.count_likes_by_post(db, [post_id]).get(post_id, 0)
    return _ok(liked=liked, count=count)


@router.post("/posts")
def create_post(
    request: Request,
    body: Annotated[str, Form()] = "",
    is_anonymous: Annotated[bool, Form()] = False,
    student: Student = Depends(require_not_banned),
    db: Session = Depends(get_db),
) -> Response:
    """Publish a post and hand back its card, ready to insert."""
    result = feed_service.create_post(db, student, body, is_anonymous)
    if not result.ok:
        response = _no(
            feed_service.refusal_message(
                result.reason, feed_service.MAX_POST_LENGTH, result.retry_after
            )
        )
        if result.retry_after:
            response.headers["Retry-After"] = str(result.retry_after)
        return response

    post = result.post
    if post is None:  # unreachable: ok is True only together with a post
        return _no("Не удалось сохранить")
    html = _fragment(
        request,
        "_post_card.html",
        post=post,
        comment_count=0,
        avatar_keys=students_repo.avatar_keys_by_id(db, [post.author_id]),
        like_counts={post.id: 0},
        liked_post_ids=set(),
        student=student,
        back="/feed",
    )
    return _ok(html=html)


@router.post("/posts/{post_id}/comments")
def create_comment(
    request: Request,
    post_id: int,
    body: Annotated[str, Form()] = "",
    is_anonymous: Annotated[bool, Form()] = False,
    student: Student = Depends(require_not_banned),
    db: Session = Depends(get_db),
) -> Response:
    """Add a comment and hand back its markup."""
    post = _visible_post(db, post_id)
    if post is None:
        return _no("Пост не найден", status.HTTP_404_NOT_FOUND)

    result = feed_service.create_comment(db, student, post, body, is_anonymous)
    if not result.ok:
        response = _no(
            feed_service.refusal_message(
                result.reason, feed_service.MAX_COMMENT_LENGTH, result.retry_after
            )
        )
        if result.retry_after:
            response.headers["Retry-After"] = str(result.retry_after)
        return response

    comment = result.comment
    if comment is None:  # unreachable: ok is True only together with a comment
        return _no("Не удалось сохранить")
    html = _fragment(
        request,
        "_comment.html",
        comment=comment,
        avatar_keys=students_repo.avatar_keys_by_id(db, [comment.author_id]),
        student=student,
    )
    total = comments_repo.count_visible_comments(db, post.id)
    return _ok(html=html, count=total)


@router.post("/assistant")
def ask_assistant(
    question: Annotated[str, Form()] = "",
    student: Student = Depends(require_not_banned),
    db: Session = Depends(get_db),
) -> Response:
    """Answer a question without leaving the page.

    The same three gates as the form: empty, too long, too often. What differs
    is the shape of the failure -- a chat cannot re-render the whole page with a
    red line at the top of it, so the refusal arrives as the answer it is: text
    in the assistant's own bubble, with ``ok`` False so the script can style it.
    """
    text = question.strip()
    if not text:
        return _no("Введите вопрос.")
    if len(text) > assistant_service.MAX_QUESTION_LENGTH:
        return _no(
            f"Слишком длинный вопрос: максимум "
            f"{assistant_service.MAX_QUESTION_LENGTH} символов."
        )

    limit = limiter.check(
        f"assistant:{student.id}",
        limit=settings.RATE_LIMIT_ASSISTANT,
        window_seconds=settings.RATE_LIMIT_ASSISTANT_WINDOW_SECONDS,
    )
    if not limit.allowed:
        response = _no(
            f"Слишком много вопросов. Попробуйте через {limit.retry_after} с.",
            status.HTTP_429_TOO_MANY_REQUESTS,
        )
        response.headers["Retry-After"] = str(limit.retry_after)
        return response

    answer = assistant_service.ask(
        db, student, text, today=datetime.now(timezone.utc).date()
    )
    # Rendered here rather than in the browser: the escaping that makes a
    # model's answer safe to show lives in one Python function
    # (app.core.markdown), and there is no second implementation of it in
    # JavaScript to keep in step.
    return _ok(source=answer.source, html=markdown.render(answer.text))


@router.post("/me/avatar")
def change_avatar(
    avatar_key: Annotated[str, Form()] = "",
    student: Student = Depends(require_not_banned),
    db: Session = Depends(get_db),
) -> Response:
    """Save the avatar the gallery had already shown as chosen."""
    result = avatars_service.set_avatar(db, student, avatar_key)
    if not result.ok:
        return _no("Такой картинки нет в галерее")
    return _ok(message="Аватарка сохранена")


@router.post("/me/alias")
def change_alias(
    new_alias: Annotated[str, Form()] = "",
    student: Student = Depends(require_not_banned),
    db: Session = Depends(get_db),
) -> Response:
    """Change the alias. Answers with the stored value, not with what was typed."""
    result = aliases_service.set_alias(db, student, new_alias)
    if not result.ok:
        return _no(aliases_service.refusal_message(result))
    return _ok(alias=student.alias, message="Псевдоним сохранён")


@router.post("/team-applications")
def apply_to_team(
    role: Annotated[str, Form()] = "",
    contact: Annotated[str, Form()] = "",
    student: Student = Depends(require_not_banned),
    db: Session = Depends(get_db),
) -> Response:
    """Take an application to the development team."""
    result = team_service.submit_application(db, student, role, contact)
    if not result.ok:
        return _no(team_service.refusal_message(result))
    return _ok(message="Заявка отправлена — мы напишем вам в Telegram")
