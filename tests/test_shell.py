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


def _rem_to_px(css: str, token: str) -> int:
    """The px value of a token declared as a plain rem length (16px root)."""
    value = re.search(re.escape(token) + r": ([\d.]+)rem;", css)
    assert value, token
    return round(float(value.group(1)) * 16)


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


def test_the_assistant_button_hangs_over_the_dock_everywhere_but_its_own_page(student):
    """One round shortcut to the assistant, and it is not on the assistant."""
    for page in ("/feed", "/me", "/esports", "/schedule"):
        html = student.client.get(page).text
        button = re.search(r'<a class="fab" href="([^"]+)"[^>]*>', html)
        assert button, page
        assert button.group(1) == "/assistant", page
        # A link, not a button a script would have to wire up -- and named for
        # a screen reader, since the icon inside is decoration.
        assert 'aria-label="Ассистент"' in button.group(0), page
        # The page knows to leave it room at the foot (see the class in the CSS
        # test below): the button is fixed, so the padding is the only thing
        # between it and the last row of the page.
        assert re.search(r'<body class="[^"]*\bhas-dock\b', html), page

    # On its own page there is nothing left to open: the button is gone. The
    # frame is still there, so the class that reserves the room stays too.
    assistant = student.client.get("/assistant").text
    assert 'class="fab"' not in assistant
    assert re.search(r'<body class="[^"]*\bhas-dock\b', assistant)


def test_a_page_without_the_frame_reserves_no_room_for_the_button(client):
    """The login screen has neither dock nor button; its card is centred."""
    html = client.get("/login").text
    assert 'class="fab"' not in html
    assert not re.search(r'<body class="[^"]*\bhas-dock\b', html)


def test_the_page_leaves_the_button_its_own_height_plus_a_gap():
    """A fixed button over a page that did not know about it covers the last
    row -- on the post page, that row is the composer's round send button."""
    layout = _css("layout.css")

    room = re.search(r"\n    body\.has-dock \{(.*?)\}", layout, re.S).group(1)
    assert "var(--dock-clearance)" in room
    assert "var(--fab-h)" in room  # the button's own height, from theme.css
    assert "var(--space-3)" in room  # and a gap above it
    assert "env(safe-area-inset-bottom)" in room


def test_the_assistant_button_hides_with_the_dock_on_a_wide_screen():
    """It is part of the phone's navigation, so it goes where the dock goes."""
    layout = _css("layout.css")
    wide = re.search(r"@media \(min-width: 768px\) \{(.*?)\n\}", layout, re.S)

    fab = re.search(r"\n\.fab \{(.*?)\}", layout, re.S).group(1)
    assert "position: fixed" in fab
    assert "border-radius: 50%" in fab
    # 48px: a circle a thumb finds without aiming, and wide enough for the tap
    # target the rest of the site keeps.
    assert "width: var(--fab-h)" in fab and "height: var(--fab-h)" in fab
    assert re.search(r"--fab-h: 3rem;", _css("theme.css"))
    assert "var(--dock-clearance)" in fab
    assert "var(--dock-lens)" in fab and "var(--dock-line)" in fab
    assert "backdrop-filter: blur(var(--dock-blur)) saturate(180%)" in fab
    assert "-webkit-backdrop-filter: blur(var(--dock-blur)) saturate(180%)" in fab

    # Hidden on a wide screen, and by the very rule that hides the dock: the
    # two belong to each other, so one cannot leave without the other.
    assert re.search(r"\.bottomnav,\s*\.fab \{\s*display: none;\s*\}", wide.group(1))
    # Without the blur it is still a button: the same opaque fallback the dock
    # uses, so it cannot turn into a see-through ghost on an old browser.
    fallbacks = [
        block.group(1)
        for block in re.finditer(
            r"@supports not \(\(backdrop-filter.*?\{(.*?)\}\s*\}", layout, re.S
        )
    ]
    assert any(".fab" in body and "var(--surface-1)" in body for body in fallbacks)


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
    # Four ways out of the gesture: the page swipe ends, the page swipe is
    # cancelled, the bar drag is dropped and the bar drag is cancelled. A path
    # that forgot one leaves the lens swollen for good.
    assert script.count('classList.remove("is-swiping")') == 4
    assert "moveBlob(wanted < 0 ? here : wanted)" in script
    # The clamp that keeps the lens inside the bar it was measured against.
    assert "nav.clientWidth - width" in script


def test_the_lens_is_measured_from_the_tab_not_counted(student):
    """Geometry comes from the tab's own rect, not from dividing the bar.

    The lens is 12px wider than the tab it stands on, so it cannot be a
    fraction of the bar: on the first and the last tab it would hang over the
    capsule's rounded end. The script centres it on the tab and clamps it.
    """
    script = student.client.get("/static/swipe.js").text

    assert "const LENS_OVERHANG = 12;" in script
    assert "getBoundingClientRect()" in script
    assert "box.width + LENS_OVERHANG" in script
    # Re-measured whenever the bar changes shape, not only on navigation.
    for trigger in ('"resize"', '"orientationchange"', '"load"', "fonts.ready"):
        assert trigger in script


def test_a_drag_is_not_a_tap(student):
    """The bar drag follows the finger, and swallows the click it produces.

    A finger that moved meant to move the lens; the click the browser fires on
    the tab it started from would then navigate somewhere else. One click is
    swallowed -- and a tap, which never moved, is left to the link.
    """
    script = student.client.get("/static/swipe.js").text

    assert "const DRAG_SLOP = 6;" in script
    assert "nav.setPointerCapture(event.pointerId)" in script
    # The flick: the same reading as the swipe, one axis over.
    assert "const FLICK = 0.55;" in script
    assert "flickDirection()" in script

    # The stop is registered in the capture phase, so it never reaches the tab.
    swallow = re.search(
        r'nav\.addEventListener\(\s*"click",(.*?)\}\s*,\s*true\s*\)', script, re.S
    )
    assert swallow, "the drag does not swallow the click that follows it"
    assert "swallowClick" in swallow.group(1)
    assert "preventDefault" in swallow.group(1)

    # And a tap is still a tap: the link is never touched while nothing moved.
    assert "if (!dragged) {" in script


def test_the_dock_floats_twelve_pixels_off_the_edges():
    theme = _css("theme.css")
    layout = _css("layout.css")

    dock = re.search(r"\.bottomnav \{(.*?)\}", layout, re.S).group(1)
    # 12px on each side, and never less than the notch in landscape.
    for edge in ("right", "left"):
        assert "max(var(--space-3), env(safe-area-inset-%s))" % edge in dock
    assert "bottom: calc(var(--space-3) + env(safe-area-inset-bottom))" in dock
    # A fixed height: the lens is taller than the tab it stands on, and a bar
    # sized by its contents would grow and shrink as the lens passed each tab.
    assert "height: var(--nav-h)" in dock
    assert re.search(r"--nav-h: 4\.25rem;", theme), "the bar is 68px tall"
    assert "padding: var(--space-2);" in dock  # 8px of air inside, all four sides

    # Without a frost to hide behind the capsule is opaque, not translucent:
    # otherwise the page's own text runs straight through the labels.
    fallback = re.search(
        r"@supports not \(\(backdrop-filter.*?\{(.*?)\}\s*\}", layout, re.S
    )
    assert fallback, "the dock has no fallback for a browser without backdrop-filter"
    assert "var(--surface-1)" in fallback.group(1)


def test_a_tab_is_26px_of_icon_and_11px_of_label():
    layout = _css("layout.css")

    icon = re.search(r"\.bottomnav \.icon \{(.*?)\}", layout, re.S).group(1)
    assert "width: 1.625rem" in icon  # 26px
    assert "height: 1.625rem" in icon

    tab = re.search(r"\.bottomnav > a \{(.*?)\}", layout, re.S).group(1)
    assert "font-size: 0.6875rem" in tab  # 11px
    assert "white-space: nowrap" in tab  # a wrapped label makes one tab taller

    # The four tabs you are not in sit back; the one you are in does not.
    dimmed = [block for block in re.findall(r"\.bottomnav > a \{(.*?)\}", layout, re.S)]
    assert any("opacity: .6" in block for block in dimmed)
    active = re.search(r"\.bottomnav \.is-active \{(.*?)\}", layout, re.S).group(1)
    assert "opacity: 1" in active


def test_the_lens_stays_out_of_the_flex_line():
    """No rule may hand the lens a place of its own in the row of tabs.

    This is the bug the bar shipped with: `.bottomnav.is-js > span` is more
    specific than `.bottomnav__blob`, and the lens *is* a span and a direct
    child of the bar -- so the rule that lifts the tabs above the lens turned
    the lens into a flex item. It then took a slot at the leading edge (the
    empty gap on the left of the bar, with the five tabs sharing what was left)
    and its transform, measured from its own slot instead of from the bar, put
    it a padding width to the right of the tab it stands for.
    """
    layout = _css("layout.css")

    # The lens is positioned against the bar and nothing else.
    blob = re.search(r"\n\.bottomnav__blob \{(.*?)\}", layout, re.S).group(1)
    assert "position: absolute" in blob
    for declaration in ("top: 50%", "height: var(--dock-lens-h)"):
        assert declaration in blob

    # And no selector reaches it through `span`: the tabs are links, so a rule
    # written for "the children of the bar" must say `a`. Comments go first --
    # this very file explains the old selector, and the explanation is not a
    # rule.
    rules = re.sub(r"/\*.*?\*/", "", layout, flags=re.S)
    for selector, _ in re.findall(r"([^{}]+)\{([^{}]*)\}", rules):
        assert "> span" not in selector, (
            "this rule would give the lens a slot of its own: %s" % selector.strip()
        )
    assert ".bottomnav.is-js > a {" in layout

    # The bar is one row of five equal tabs and nothing else: no spacer, no
    # hidden item, and the same padding on both sides.
    dock = re.search(r"\.bottomnav \{(.*?)\}", layout, re.S).group(1)
    assert "padding: var(--space-2);" in dock
    assert "justify-content: space-between" in dock
    assert "height: var(--nav-h)" in dock
    # The lens has to fit *inside* the bar with room to spare: 56px in 68px
    # leaves 6px above and below, and the bar's own 8px of padding leaves it
    # 6px clear of either end of its travel.
    theme = _css("theme.css")
    bar_px = _rem_to_px(theme, "--nav-h")
    lens_px = _rem_to_px(theme, "--dock-lens-h")
    assert bar_px == 68 and lens_px == 56
    assert lens_px <= bar_px - 8


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
    # The lens: brighter than the control it stands for, with a rim of its own.
    assert "--dock-lens:" in theme
    assert "--dock-lens-rim:" in theme
    assert "--dock-lens-h:" in theme

    dock = re.search(r"\.bottomnav \{(.*?)\}", layout, re.S).group(1)
    for declaration in (
        "var(--dock)",
        "var(--dock-line)",
        "var(--radius-dock)",
        "var(--dock-shadow)",
        "var(--dock-blur)",
        "var(--space-3)",
    ):
        assert declaration in dock

    # Every tab is already the capsule the selected one turns into: the
    # highlight only thickens the fill and lights the rim, so picking a tab
    # moves nothing. That is why the corner is on the tab and not on `.is-active`.
    tab = re.search(r"\.bottomnav > a \{(.*?)\}", layout, re.S)
    assert "var(--radius-dock-tab)" in tab.group(1)
    assert "border: 1px solid transparent" in tab.group(1)

    active = re.search(r"\.bottomnav \.is-active \{(.*?)\}", layout, re.S).group(1)
    assert "var(--dock-line)" in active
    assert "var(--dock-lens)" in active


def test_the_pill_lifts_inside_the_bar_and_comes_home_on_the_spring():
    layout = _css("layout.css")

    lift = re.search(
        r"\.bottomnav\.is-swiping \.bottomnav__blob \{(.*?)\}", layout, re.S
    ).group(1)
    assert "scale: 1.15" in lift
    # The lift cannot live in `transform`: the script rewrites that every frame
    # while the finger is down, and it would be wiped out by the movement it is
    # supposed to accompany. `transform-origin` is not that property.
    assert re.search(r"(?<![-\w])transform:", lift) is None
    assert "transition: none" in lift

    blob = re.search(r"\.bottomnav__blob \{(.*?)\}", layout, re.S).group(1)
    assert "scale var(--transition-dock)" in blob
    # 68px of bar, 56px of lens: the lift has room to grow without spilling
    # over the capsule's edge.
    assert re.search(r"--dock-lens-h: 3\.5rem;", _css("theme.css"))


def test_the_dock_clears_the_content_by_the_same_token():
    """Page, fade, capsule and the assistant button agree on one number."""
    theme = _css("theme.css")
    layout = _css("layout.css")

    clearance = re.search(r"--dock-clearance: (calc\([^;]*\));", theme).group(1)
    assert "var(--nav-h)" in clearance
    # 68px of bar plus 36px of air: the 12px the capsule floats by, and the
    # 24px that keeps the last control of a page out from under it.
    assert "2.25rem" in clearance
    # The padding, the fade, and the round assistant button -- which floats in
    # exactly the air the padding leaves, so it hangs over nothing.
    assert layout.count("var(--dock-clearance)") == 3


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


# --- Pass 3: the phone reads as an app -----------------------------------------


def _media(css: str, query: str) -> str:
    """Everything inside the first ``@media (query)`` block.

    Braces are counted rather than matched with a regex: every block worth
    reading here has rules of its own inside it, and a lazy ``.*?}`` would stop
    at the first rule's closing brace.
    """
    start = css.index("@media (" + query + ") {")
    depth = 0
    for index in range(css.index("{", start), len(css)):
        if css[index] == "{":
            depth += 1
        elif css[index] == "}":
            depth -= 1
            if depth == 0:
                return css[start : index + 1]
    raise AssertionError("unterminated media query")


def test_the_phone_gets_a_type_scale_of_its_own():
    """17px body and a 34px title: the phone is not a narrow laptop."""
    theme = _css("theme.css")

    phone = _media(theme, "max-width: 767px")
    assert "--step-0: 1.0625rem;" in phone      # 17px: iOS body text
    assert "--step-4: 2.125rem;" in phone       # 34px: iOS Large Title
    assert "--step--1: 0.8125rem;" in phone     # 13px: a caption

    # 360px is where a title and the chip beside it stop fitting, and the title
    # is what gives way -- two pixels of it, and nothing else on the page.
    narrow = _media(theme, "max-width: 380px")
    assert "--step-4: 1.875rem;" in narrow
    assert "--step-0" not in narrow


def test_a_row_of_the_list_is_a_thumb_tall():
    """44px, and the same number for the header's button and the round send."""
    theme = _css("theme.css")
    components = _css("components.css")

    assert _rem_to_px(theme, "--row-h") == 44

    row = re.search(r"\.list-row \{(.*?)\}", components, re.S).group(1)
    assert "min-height: var(--row-h)" in row

    icon_button = re.search(r"\.btn--icon \{(.*?)\}", components, re.S).group(1)
    assert "var(--row-h)" in icon_button

    # The chevron is punctuation at the end of a row, not an icon to look at.
    chevron = re.search(r"\.list-row \.icon--chevron \{(.*?)\}", components, re.S).group(1)
    assert "1.125rem" in chevron


def test_the_profile_is_one_list_of_rows(student):
    """Every door on the profile is a row of the same list, not a card of its own."""
    html = student.client.get("/me").text
    main = re.search(r'<main class="container">(.*)</main>', html, re.S).group(1)

    # The class, six sections and the invitation: each one a row with its own
    # chevron, and the settings row with a chevron too -- nine rows that lead
    # somewhere. The class row sits above the sections and outside the dock (see
    # app.routers.auth): the section is opened from the profile, like the
    # settings, and the dock keeps its five links.
    assert len(re.findall(r'class="list-row list-row--link"', main)) == 8
    assert main.count("icon--chevron") == 9

    # The way out is a row as well, and the only red one: it is the one thing on
    # the screen that cannot be undone.
    assert 'class="list-row list-row--danger"' in main
    assert main.count("list-row--danger") == 1

    # The captions are over the groups, not headings inside them -- one per
    # group, and the awards group only exists for a student who has a badge.
    assert main.count('class="list-head"') == 5
    assert "link-grid" not in main

    # And the two sheets still open from their rows.
    assert 'data-dialog-open="settings"' in main
    assert 'data-dialog-open="dev-apply"' in main


def test_the_assistant_input_is_the_feed_composer_and_it_is_pinned(student):
    """One composer for the whole site, and on a phone it does not scroll away."""
    html = student.client.get("/assistant").text

    form = re.search(r'<form[^>]*data-async="assistant".*?</form>', html, re.S).group(0)
    assert "composer__form" in form          # the feed's own row
    assert "data-autogrow" in form           # one line that grows to four
    assert 'class="btn--icon btn--send"' in form
    assert 'class="visually-hidden">Спросить' in form

    components = _css("components.css")
    # The old row of its own is gone -- there is no second composer left to
    # drift away from the first.
    assert "composer-foot" not in html
    assert ".composer-foot" not in components

    # Pinned above the dock, by the token the dock itself is measured with.
    pinned = [
        block
        for block in re.findall(r"\.chat-composer \{(.*?)\}", components, re.S)
        if "position: sticky" in block
    ]
    assert len(pinned) == 1, "the composer is pinned once or not at all"
    assert "var(--dock-clearance)" in pinned[0]


def test_a_panel_on_a_phone_is_inset_like_a_card():
    """16px of padding at phone widths, and the rows still reach their edge."""
    components = _css("components.css")
    phone = _media(components, "max-width: 767px")

    assert "padding: var(--space-4);" in phone

    # `.list` comes after that block and takes the padding off again: a row has
    # to reach the rounded corner, or the group is a frame inside a frame.
    list_rule = re.search(r"\.list \{(.*?)\}", components, re.S).group(1)
    assert "padding: 0" in list_rule
    assert components.index(".list {") > components.index(phone)

