"""Tests for the assistant's Markdown renderer.

The renderer exists to be safe, not to be complete, so the escaping tests are
the point of this file: whatever a model writes, the answer must reach the page
as text about angle brackets and never as markup. The formatting tests are there
to keep the other half honest -- a renderer that escaped everything would pass
every safety check and show ``**bold**`` to the student.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core import markdown  # noqa: E402


def test_script_tag_comes_out_as_text():
    rendered = markdown.render("<script>alert(1)</script>")
    assert "<script>" not in rendered
    assert "&lt;script&gt;" in rendered


def test_an_image_with_a_handler_is_not_an_image():
    rendered = markdown.render('<img src=x onerror="alert(1)">')
    assert "<img" not in rendered
    assert "onerror" in rendered  # as text, escaped, inert


def test_ampersands_are_escaped_but_quotes_are_left_alone():
    # quote=False on purpose: no attribute is ever built out of this text, so
    # turning " into an entity would only make the answer noisier on screen.
    assert markdown.render('a & b "c"') == '<p>a &amp; b "c"</p>'


def test_html_inside_a_code_span_is_still_escaped():
    # The parked code span goes back in as it was escaped, not as it was typed:
    # this is the one place where raw text is re-inserted after formatting.
    rendered = markdown.render("`<b>bold</b>`")
    assert rendered == "<p><code>&lt;b&gt;bold&lt;/b&gt;</code></p>"


def test_bold_becomes_a_strong_tag():
    assert markdown.render("это **важно**") == "<p>это <strong>важно</strong></p>"


def test_a_code_span_shields_its_asterisks():
    assert markdown.render("`a ** b`") == "<p><code>a ** b</code></p>"


def test_bullets_become_one_list():
    # Six lines, one block: the renderer joins its blocks with newlines, and the
    # two items share one <ul> rather than each getting their own.
    assert markdown.render("- раз\n- два") == (
        "<ul>\n<li>раз</li>\n<li>два</li>\n</ul>"
    )


def test_numbered_items_become_an_ordered_list():
    assert markdown.render("1. раз\n2. два") == (
        "<ol>\n<li>раз</li>\n<li>два</li>\n</ol>"
    )


def test_a_decimal_is_not_a_list():
    # "1.5" has no space after the marker, so it is a paragraph and not an item.
    assert markdown.render("1.5 кг") == "<p>1.5 кг</p>"


def test_a_heading_becomes_h3():
    assert markdown.render("## Итог") == "<h3>Итог</h3>"


def test_a_single_newline_is_a_line_break():
    # The school-database answers are written one fact per line and depend on it.
    assert markdown.render("первая\nвторая") == "<p>первая<br>вторая</p>"


def test_a_blank_line_starts_a_new_paragraph():
    assert markdown.render("первая\n\nвторая") == "<p>первая</p>\n<p>вторая</p>"


def test_a_paragraph_ends_a_list():
    rendered = markdown.render("- одно\nпродолжение")
    assert rendered == "<ul>\n<li>одно</li>\n</ul>\n<p>продолжение</p>"


def test_empty_text_renders_nothing():
    assert markdown.render("") == ""
    assert markdown.render("   \n  ") == ""
