"""Tests for the web app manifest and the "add to home screen" wiring.

Installing the site goes wrong silently: a manifest the browser cannot read, an
icon the manifest names but the disk does not have, or a page that never
mentions any of it all end the same way -- a screenshot for a home screen icon
and a link that opens in Safari with the address bar showing. Nothing here is
visible in a test run of the server, so it is asserted instead.
"""

import re
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app" / "static"


def _manifest(client) -> dict:
    response = client.get("/manifest.json")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/manifest+json")
    return response.json()


def test_manifest_is_served_from_the_root(client):
    # It is read without a session: the browser fetches it before the reader
    # has logged in anywhere, and so does the "add to home screen" flow.
    manifest = _manifest(client)

    # "/" is the app. start_url and scope are read against the root the manifest
    # is served from, and a start_url outside the scope is what throws the
    # reader out of the installed shell on the very first tap.
    assert manifest["start_url"] == "/"
    assert manifest["scope"] == "/"
    assert manifest["display"] == "standalone"
    assert manifest["name"]
    assert manifest["short_name"]


def test_the_manifest_icons_are_files_that_exist(client):
    manifest = _manifest(client)
    assert manifest["icons"], "an installable app needs at least one icon"

    for icon in manifest["icons"]:
        assert icon["src"].startswith("/static/"), icon["src"]
        path = STATIC / icon["src"].removeprefix("/static/")
        assert path.is_file(), f"the manifest names a missing icon: {icon['src']}"

        # The declared size has to be the real one -- this is the number the
        # browser chooses the icon by, and a wrong one means no icon at all.
        # Read straight out of the PNG header rather than with an image library:
        # the test suite has no business gaining a dependency for this.
        width, height = struct.unpack(">II", path.read_bytes()[16:24])
        assert f"{width}x{height}" == icon["sizes"], icon["src"]


def test_the_manifest_carries_the_sizes_installers_ask_for(client):
    manifest = _manifest(client)
    sizes = {icon["sizes"] for icon in manifest["icons"]}
    purposes = {icon.get("purpose") for icon in manifest["icons"]}

    # 192 and 512 are what Chrome wants before it will offer the install prompt.
    assert {"192x192", "512x512"} <= sizes
    # Android masks the icon to its own shape; an unmasked icon gets a grey
    # square behind it.
    assert "maskable" in purposes


def test_the_app_starts_on_the_feed_and_not_on_a_login_for_a_session(client, student):
    # start_url "/" is the same entry point as the site itself: a redirect for
    # whoever arrives, and for a student that redirect is the feed.
    response = student.client.get("/", follow_redirects=False)
    assert response.headers["location"] == "/feed"


def test_an_anonymous_visitor_is_sent_to_the_login_form(client):
    response = client.get("/", follow_redirects=False)
    assert response.headers["location"] == "/login"


def test_pages_hand_ios_everything_it_needs_to_install(client):
    page = client.get("/login").text

    assert '<link rel="manifest" href="/manifest.json">' in page
    assert 'rel="apple-touch-icon"' in page
    # iOS reads the manifest from 16.4 on, but the name, the icon and the status
    # bar come from these tags -- without them the icon is a screenshot of the
    # page and the bar is opaque.
    assert 'name="apple-mobile-web-app-capable" content="yes"' in page
    assert 'name="apple-mobile-web-app-status-bar-style" content="black-translucent"' in page
    assert 'name="apple-mobile-web-app-title"' in page

    # Without viewport-fit=cover every env(safe-area-inset-*) in the stylesheets
    # is zero in Safari, and the bars the layout keeps clear of the notch are
    # laid out for insets that never arrive.
    assert "viewport-fit=cover" in page


def test_the_pages_that_are_installed_never_leave_the_shell():
    """Every page a student can reach is on the site itself.

    An installed app stays inside its shell for same-origin links and leaves it
    for anything else: a link with ``target="_blank"``, or a login handed to an
    outside provider. The login form here is the site's own (the OAuth buttons
    are placeholders and disabled), so nothing in a template may open a new
    browsing context -- that is what this checks, over every template at once.
    """
    offenders = []
    for path in sorted((ROOT / "app" / "templates").rglob("*.html")):
        text = path.read_text(encoding="utf-8")
        # Jinja comments never reach the browser, and prose in them may well
        # mention the attribute while explaining why there must not be one.
        text = re.sub(r"\{#.*?#\}", "", text, flags=re.S)
        for match in re.finditer(r"target\s*=\s*[\"']_blank[\"']", text):
            line = text.count("\n", 0, match.start()) + 1
            offenders.append(f"{path.name}:{line}")
    assert offenders == [], f"a blank target leaves the installed app: {offenders}"
