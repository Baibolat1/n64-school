"""The contract between the pages and app/static/app.js.

A page that loses its ``data-async`` attribute does not break: it goes back to
an ordinary form submission and the site still works, which is exactly why
nobody would notice. Nothing reloads when it never stopped reloading. So the
wiring is asserted here rather than left to chance -- for every form that is
supposed to stay put, that it names a handler, that the handler is one the
script knows, and that the JSON twin is under /api.

The other half of the same contract is the ladder: each of those forms must
still keep a real ``action`` pointing at a real page. Without it, a browser that
never runs the script gets a JSON answer drawn as a page.
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Every handler app/static/app.js dispatches on. A page using a name that is not
# in here collects no handler and silently falls back to a full submit.
HANDLERS = {"like", "post", "comment", "assistant", "avatar", "alias", "team"}


def _forms(html: str) -> list[str]:
    return re.findall(r"<form\b.*?</form>", html, re.S)


def _async_forms(html: str) -> list[str]:
    return [form for form in _forms(html) if "data-async" in form]


def _attr(form: str, name: str) -> str | None:
    match = re.search(rf'{name}="([^"]*)"', form)
    return match.group(1) if match else None


def _assert_async_form(form: str, kind: str) -> None:
    assert _attr(form, "data-async") == kind
    api = _attr(form, "data-api")
    assert api is not None and api.startswith("/api/"), form

    # The real page behind the same form: a form with no action, or one that
    # posts to the JSON endpoint, is a form that only works with a script.
    action = _attr(form, "action")
    assert action and not action.startswith("/api/"), form


def _post_id(session_factory, student_id) -> int:
    from app.models.content import Post

    with session_factory() as db:
        post = Post(
            author_id=student_id,
            display_alias="Ученик-01",
            is_anonymous=False,
            body="Тема",
        )
        db.add(post)
        db.commit()
        return post.id


def test_the_script_is_served_and_takes_the_short_way_round(student):
    html = student.client.get("/feed").text
    assert '<script src="/static/app.js"></script>' in html
    # Without the slot there is nowhere for "сохранено" to appear, and every
    # handler that reports back would talk to a null element.
    assert 'id="toast-host"' in html

    script = student.client.get("/static/app.js")
    assert script.status_code == 200
    # The one thing the whole file exists to avoid.
    assert "window.location.reload" not in script.text
    assert "location.reload" not in script.text


def test_the_composer_publishes_without_reloading(student):
    forms = _async_forms(student.client.get("/feed").text)
    assert any(_attr(form, "data-async") == "post" for form in forms)

    composer = next(form for form in forms if _attr(form, "data-async") == "post")
    _assert_async_form(composer, "post")
    assert 'id="posts"' in student.client.get("/feed").text


def test_the_like_button_toggles_without_reloading(student, session_factory):
    post_id = _post_id(session_factory, student.student_id)
    html = student.client.get("/feed").text

    like = next(
        form for form in _async_forms(html) if _attr(form, "data-async") == "like"
    )
    _assert_async_form(like, "like")
    assert _attr(like, "data-api") == f"/api/posts/{post_id}/like"
    # The page the like falls back to has to be the page the card is on.
    assert _attr(like, "action") == f"/feed/posts/{post_id}/like"


def test_the_post_page_comments_without_reloading(student, session_factory):
    post_id = _post_id(session_factory, student.student_id)
    html = student.client.get(f"/feed/posts/{post_id}").text

    comment = next(
        form for form in _async_forms(html) if _attr(form, "data-async") == "comment"
    )
    _assert_async_form(comment, "comment")
    assert _attr(comment, "data-api") == f"/api/posts/{post_id}/comments"
    # Both places the script writes into: the list it appends to and the number
    # in the heading above it.
    assert 'id="comments"' in html
    assert 'id="comments-count"' in html


def test_the_assistant_asks_without_reloading(student):
    html = student.client.get("/assistant").text

    form = next(
        form
        for form in _async_forms(html)
        if _attr(form, "data-async") == "assistant"
    )
    _assert_async_form(form, "assistant")
    assert 'id="chat"' in html


def test_the_profile_changes_without_reloading(student):
    html = student.client.get("/me").text
    kinds = {_attr(form, "data-async") for form in _async_forms(html)}

    assert {"avatar", "alias", "team"} <= kinds
    for form in _async_forms(html):
        _assert_async_form(form, _attr(form, "data-async"))


def test_every_handler_on_every_page_is_one_the_script_knows(student, session_factory):
    post_id = _post_id(session_factory, student.student_id)

    for page in (
        "/feed",
        f"/feed/posts/{post_id}",
        "/assistant",
        "/me",
        "/me/settings",
        "/me/dev-apply",
    ):
        for form in _async_forms(student.client.get(page).text):
            assert _attr(form, "data-async") in HANDLERS, (page, form)
