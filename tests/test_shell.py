"""The frame around every page: the dock, the header menu and the sheets.

Two things are asserted here that no screen-by-screen test would catch. The
first is that the frame is *everywhere* -- a page that forgot the dock or the
menu still renders, it just leaves the reader with no way out -- so the dock and
the menu are checked on a page from each corner of the site.

The second is the shape of the profile: the settings forms exist once, are
reachable two ways (the sheet on /me and the page at /me/settings), and both
ways post to the same route. A profile that grew a second copy of the gallery
would still pass every other test in the suite.
"""

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core import avatars as avatars_gallery  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CSS_DIR = ROOT / "app" / "static" / "css"

# One page from each part of the site: a tab of the dock, a page under a tab,
# and the profile. The frame is the same on all of them.
PAGES = ("/feed", "/assistant", "/me")


def _css(name: str) -> str:
    return (CSS_DIR / name).read_text(encoding="utf-8")


def _settings_dialog(html: str) -> str:
    """The settings sheet only, so a form outside it cannot pass as inside it."""
    match = re.search(r'<dialog[^>]*id="settings".*?</dialog>', html, re.S)
    assert match, "the profile renders no settings sheet"
    return match.group(0)


# --- The frame ----------------------------------------------------------------


@pytest.mark.parametrize("page", PAGES)
def test_every_page_carries_the_dock_and_the_menu(student, page):
    html = student.client.get(page).text

    # The dock: five tabs, each a real link, the way it is without any script.
    dock = re.search(r'<nav class="bottomnav">(.*?)</nav>', html, re.S)
    assert dock, page
    assert len(re.findall(r"<a href=", dock.group(1))) == 5, page

    # The menu: a <details>, so it opens with no script at all, and it says
    # what the site is and how to join the team.
    assert '<details class="burger">' in html, page
    panel = re.search(r'<div class="burger-panel">(.*?)</div>\s*</details>', html, re.S)
    assert panel, page
    assert "/me/dev-apply" in panel.group(1), page


def test_the_menu_needs_no_script_to_open(student):
    """The panel is a <details>, not a button that a script has to wire up."""
    script = student.client.get("/static/app.js").text
    # The script only closes it again: it never has to open a menu that does
    # not open on its own.
    assert "details.burger[open]" in script
    assert "showModal" in script


def test_a_section_has_one_name(student):
    """The dock, the header and the profile call /esports the same thing.

    One destination under two names is the same as two destinations: the reader
    has to work out that "Игры" and "Турниры" lead to the same page.
    """
    dock = student.client.get("/feed").text
    profile = student.client.get("/me").text

    for name in ("Турниры", "Игры"):
        assert (name in dock) == (name in profile), name
    assert "Турниры" in dock

    # And the page itself agrees with both.
    assert "Турниры" in student.client.get("/esports").text


def test_the_pill_follows_the_finger_and_lifts(student):
    """The drag is the same gesture as the swipe, drawn on the bar.

    The script has three jobs here and all three are checked: it moves the pill
    to the tab the finger is over, it tells the CSS that a drag is happening so
    the pill can lift, and it lets go -- a class that is added and never removed
    would leave the pill swollen for good.
    """
    script = student.client.get("/static/swipe.js").text

    assert 'nav.classList.add("is-swiping")' in script
    assert script.count('classList.remove("is-swiping")') == 2  # release and cancel
    assert "moveBlob(wanted < 0 ? here : wanted)" in script
    # The clamp that keeps the pill inside the bar it was measured against.
    assert "nav.clientWidth - box.width" in script


# --- The profile ---------------------------------------------------------------


def test_the_profile_scroll_holds_no_settings_form(student):
    """The gallery and the alias field live in the sheet, not in the scroll."""
    html = student.client.get("/me").text

    # Every copy of the picker is inside the settings dialog.
    outside = html.replace(_settings_dialog(html), "")
    assert "avatar-picker" not in outside
    assert 'name="new_alias"' not in outside
    # And the profile names both, so nobody has to guess where they went.
    assert "Открыть настройки" in html
    assert 'href="/me/settings"' in html
    assert 'data-dialog-open="settings"' in html


def test_the_settings_page_stands_on_its_own(student):
    response = student.client.get("/me/settings")

    assert response.status_code == 200
    html = response.text
    assert 'action="/me/alias"' in html
    assert 'action="/me/avatar"' in html
    assert 'data-async="alias"' in html
    assert 'data-async="avatar"' in html
    # The whole gallery, once.
    values = re.findall(r'name="avatar_key" value="([^"]*)"', html)
    assert len([value for value in values if value]) == len(avatars_gallery.AVATAR_KEYS)
    assert values.count("") == 1


def test_the_settings_page_needs_a_session(client):
    response = client.get("/me/settings", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_both_ways_in_offer_the_same_form(student):
    """The sheet on /me and the page include one partial, so they cannot drift."""
    sheet = _settings_dialog(student.client.get("/me").text)
    page = student.client.get("/me/settings").text

    for marker in ('action="/me/alias"', 'action="/me/avatar"', 'name="new_alias"'):
        assert marker in sheet
        assert marker in page


def test_a_refusal_comes_back_to_the_page_that_has_the_field(
    student, session_factory
):
    """Without a script the answer is a page, and it has to be the right one.

    The profile carries no field of its own any more, so a refusal rendered
    there would be a message about something the reader cannot see.
    """
    response = student.post("/me/avatar", "/me/settings", {"avatar_key": "nope"})

    assert response.status_code == 200
    assert "Такой картинки нет в галерее" in response.text
    assert 'name="avatar_key" value="nope"' not in response.text
    assert "Открыть настройки" not in response.text


def test_the_alias_refusal_echoes_what_was_typed(student):
    response = student.post("/me/alias", "/me/settings", {"new_alias": "аб"})

    assert response.status_code == 200
    assert 'value="аб"' in response.text


# --- The stylesheet ------------------------------------------------------------


def test_the_stylesheet_forces_nothing():
    """No `!important` anywhere: the design is carried by specificity.

    Every rule in this project is one selector and one value on a token; a
    declaration that has to shout over the others is a sign the tokens are
    wrong, not a reason to shout.
    """
    for path in sorted(CSS_DIR.glob("*.css")):
        assert "!important" not in path.read_text(encoding="utf-8"), path.name


def test_the_dock_is_a_capsule_of_its_own_material():
    theme = _css("theme.css")
    layout = _css("layout.css")

    # Its own fill, rim, blur and corner -- not the bars' page-coloured one.
    for token in ("--dock:", "--dock-line:", "--dock-blur:", "--dock-shadow:"):
        assert token in theme
    assert "--radius-dock:" in theme
    assert "--radius-dock-tab:" in theme

    dock = re.search(r"\.bottomnav \{(.*?)\}", layout, re.S).group(1)
    for declaration in (
        "var(--dock)",
        "var(--dock-line)",
        "var(--radius-dock)",
        "var(--dock-shadow)",
        "var(--dock-blur)",
        "var(--space-4)",
    ):
        assert declaration in dock

    # Every tab is already the capsule the selected one turns into: the
    # highlight only thickens the fill and lights the rim, so picking a tab
    # moves nothing. That is why the corner is on the tab and not on `.is-active`.
    tab = re.search(r"\.bottomnav > a,\n\.bottomnav > span \{(.*?)\}", layout, re.S)
    assert "var(--radius-dock-tab)" in tab.group(1)
    assert "border: 1px solid transparent" in tab.group(1)

    active = re.search(r"\.bottomnav \.is-active \{(.*?)\}", layout, re.S).group(1)
    assert "var(--control-line)" in active
    assert "var(--control-hover)" in active


def test_the_pill_lifts_inside_the_bar_and_comes_home_on_the_spring():
    layout = _css("layout.css")

    lift = re.search(
        r"\.bottomnav\.is-swiping \.bottomnav__blob \{(.*?)\}", layout, re.S
    ).group(1)
    assert "scale: 1.12" in lift
    # The lift cannot live in `transform`: the script rewrites that every frame
    # while the finger is down, and it would be wiped out by the movement it is
    # supposed to accompany. `transform-origin` is not that property.
    assert re.search(r"(?<![-\w])transform:", lift) is None
    assert "transition: none" in lift

    blob = re.search(r"\.bottomnav__blob \{(.*?)\}", layout, re.S).group(1)
    assert "scale var(--transition-spring)" in blob


def test_the_dock_clears_the_content_by_the_same_token():
    """Page, fade and capsule agree on one number, so none can drift."""
    theme = _css("theme.css")
    layout = _css("layout.css")

    clearance = re.search(r"--dock-clearance: (calc\([^;]*\));", theme).group(1)
    assert "var(--nav-h)" in clearance
    assert "5.25rem" in clearance  # 56px of bar plus 84px of air = 140px
    assert layout.count("var(--dock-clearance)") == 2  # the padding and the fade


def test_the_controls_share_one_pill():
    """The like, the counter and the complaint are the same shape as a button."""
    components = _css("components.css")

    action = re.search(r"\n\.action \{(.*?)\}", components, re.S).group(1)
    button = re.search(r"\nbutton,\n\.btn \{(.*?)\}", components, re.S).group(1)

    for declaration in ("var(--radius-pill)", "var(--control)", "var(--control-line)"):
        assert declaration in action
        assert declaration in button
    # Same padding, same size: the row of actions is a row of one control.
    assert "padding: var(--space-2) var(--space-3)" in action
    assert "font: inherit" in action
