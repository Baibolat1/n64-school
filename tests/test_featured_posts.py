"""Tests for pinned ("featured") posts: the pin, the badge and the pagination.

The pin is the one property of a post that fights with the feed's own order, so
the interesting half of this file is about the two of them together: a pinned
post is older than everything it is raised above, and it must not come round a
second time when the stream reaches its age. Both halves are checked -- the
repository, where the page is assembled, and the feed itself, because the card
the student gets is what the feature is for.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models.content import (  # noqa: E402
    CONTENT_STATUS_HIDDEN,
    CONTENT_STATUS_VISIBLE,
    Post,
)
from app.models.student import Student  # noqa: E402
from app.repositories import posts as posts_repo  # noqa: E402
from app.routers.feed import PAGE_SIZE  # noqa: E402


def _make_student(session_factory, alias="Ученик-Автор") -> int:
    with session_factory() as db:
        student = Student(alias=alias)
        db.add(student)
        db.commit()
        return student.id


def _make_post(
    session_factory,
    author_id: int,
    body: str,
    *,
    featured: bool = False,
    status: str = CONTENT_STATUS_VISIBLE,
) -> int:
    with session_factory() as db:
        post = Post(
            author_id=author_id,
            display_alias="Ученик-Автор",
            is_anonymous=False,
            body=body,
            status=status,
            is_featured=featured,
        )
        db.add(post)
        db.commit()
        return post.id


def test_a_pinned_post_comes_first_though_it_is_the_oldest(session_factory):
    author = _make_student(session_factory)
    # Written before the others, so it is last by time -- and first in the feed.
    pinned = _make_post(session_factory, author, "Идея: свободная пятница", featured=True)
    others = [_make_post(session_factory, author, f"Обычный пост {i}") for i in range(3)]

    with session_factory() as db:
        page = posts_repo.list_feed_page(db, limit=10, offset=0)

    assert page[0].id == pinned
    assert [post.id for post in page[1:]] == list(reversed(others))


def test_a_pinned_post_is_shown_once_across_the_pages(session_factory):
    # The case the offset arithmetic exists for: the stream is long enough to
    # reach the age of the pinned post, and it must not be met there again.
    author = _make_student(session_factory)
    pinned = _make_post(session_factory, author, "Идея: свободная пятница", featured=True)
    for i in range(PAGE_SIZE * 2):
        _make_post(session_factory, author, f"Обычный пост {i}")

    with session_factory() as db:
        pages = [
            posts_repo.list_feed_page(db, limit=PAGE_SIZE, offset=page * PAGE_SIZE)
            for page in range(3)
        ]

    assert pages[0][0].id == pinned
    seen = [post.id for page in pages for post in page]
    assert len(seen) == len(set(seen)), "a post was shown twice"
    # Every post, and nothing but the posts: the pin displaces a stream post
    # from the first page rather than being added on top of one.
    assert len(seen) == PAGE_SIZE * 2 + 1
    assert pinned not in [post.id for page in pages[1:] for post in page]


def test_a_pin_does_not_add_a_page(student, session_factory):
    # ``total`` keeps counting the pinned post, so one post per page stays true:
    # twenty-one posts are two pages with a pin and two without it.
    _make_post(session_factory, student.student_id, "Закреп", featured=True)
    for i in range(PAGE_SIZE):
        _make_post(session_factory, student.student_id, f"Обычный пост {i}")

    html = student.client.get("/feed").text

    assert "Страница 1 из 2" in html


def test_the_feed_marks_the_pinned_post_and_puts_it_first(student, session_factory):
    _make_post(session_factory, student.student_id, "Идея: свободная пятница", featured=True)
    _make_post(session_factory, student.student_id, "Обычный пост")

    html = student.client.get("/feed").text

    assert "post-card--featured" in html
    assert "Важное" in html
    assert html.index("Идея: свободная пятница") < html.index("Обычный пост")


def test_an_ordinary_post_wears_no_badge(student, session_factory):
    post_id = _make_post(session_factory, student.student_id, "Обычный пост")

    feed = student.client.get("/feed").text
    page = student.client.get(f"/feed/posts/{post_id}").text

    assert "post-card--featured" not in feed
    assert "Важное" not in feed
    assert "Важное" not in page


def test_the_post_page_carries_the_same_badge(student, session_factory):
    post_id = _make_post(session_factory, student.student_id, "Идея: свободная пятница", featured=True)

    html = student.client.get(f"/feed/posts/{post_id}").text

    assert "post-card--featured" in html
    assert "Важное" in html


def test_a_hidden_pinned_post_leaves_the_feed_and_the_pin_list(student, session_factory):
    # A moderator hiding a pinned post unpins it for the reader without any
    # extra bookkeeping: the visible filter sits inside the same queries.
    _make_post(
        session_factory,
        student.student_id,
        "Идея: свободная пятница",
        featured=True,
        status=CONTENT_STATUS_HIDDEN,
    )

    html = student.client.get("/feed").text

    assert "Идея: свободная пятница" not in html
    assert "post-card--featured" not in html


def test_set_featured_pins_and_unpins(session_factory):
    author = _make_student(session_factory)
    post_id = _make_post(session_factory, author, "Идея: свободная пятница")

    with session_factory() as db:
        post = posts_repo.get_post(db, post_id)
        posts_repo.set_featured(db, post, True)
        db.commit()
    with session_factory() as db:
        assert posts_repo.get_post(db, post_id).is_featured is True
        assert posts_repo.count_featured_posts(db) == 1

    with session_factory() as db:
        post = posts_repo.get_post(db, post_id)
        posts_repo.set_featured(db, post, False)
        db.commit()
    with session_factory() as db:
        assert posts_repo.get_post(db, post_id).is_featured is False
        assert posts_repo.count_featured_posts(db) == 0
