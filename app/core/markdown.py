"""A very small Markdown renderer, for the assistant's answers only.

Why this exists at all: the model writes its answer in Markdown -- numbered
lists, ``**bold**``, ``code`` -- and showing that source verbatim to a student
looks like a bug. Rendering it in the browser would mean shipping a Markdown
library and letting it build HTML from the model's text, which is exactly the
thing this site does not do (the CSP refuses inline script; there is no build
step; nothing outside ``/static`` is loaded).

Why it is safe: **every character of the answer is escaped before any tag is
put in.** The renderer never passes a byte of the model's text through as
markup -- it only ever emits tags it wrote itself, from a fixed set
(``p``, ``br``, ``h3``, ``ul``, ``ol``, ``li``, ``strong``, ``code``, ``pre``).
An answer containing ``<script>`` or ``<img onerror=...>`` comes out as visible
text about angle brackets, and the ordering cannot be flipped because escaping
happens first and the formatting patterns afterwards.

It is deliberately not a Markdown implementation: no links, no images, no HTML
passthrough, no tables, no blockquotes. What the assistant actually produces is
paragraphs, lists, bold and code, and everything else would be surface with no
caller.
"""

import html
import re

# A bullet: "- ", "* " or the "•" the help text is written with.
_BULLET = re.compile(r"^[-*•]\s+(.*)$")
# "1. " or "1) ". The digit run must be followed by the marker *and a space*, so
# a decimal like "1.5" is not read as the start of a list.
_ORDERED = re.compile(r"^\d+[.)]\s+(.*)$")
_HEADING = re.compile(r"^#{1,6}\s+(.*)$")
_CODE_SPAN = re.compile(r"`([^`]+)`")
_BOLD = re.compile(r"\*\*([^*]+)\*\*")
# Where a code span is parked while bold is applied, so that a ``**`` inside a
# code span stays code. NUL is stripped from the input before this is used, so
# the model cannot forge one of these.
_PARKED = "\x00"

_HEADING_TAG = "h3"  # the answer is a section of a panel, never a document


def _inline(text: str) -> str:
    """Escape one line of the answer, then apply the two inline patterns."""
    text = html.escape(text, quote=False)

    parked: list[str] = []

    def park(match: re.Match[str]) -> str:
        parked.append(match.group(1))
        return f"{_PARKED}{len(parked) - 1}{_PARKED}"

    text = _CODE_SPAN.sub(park, text)
    text = _BOLD.sub(r"<strong>\1</strong>", text)

    def unpark(match: re.Match[str]) -> str:
        # The parked text is already escaped, so it goes back in as it is.
        return f"<code>{parked[int(match.group(1))]}</code>"

    return re.sub(rf"{_PARKED}(\d+){_PARKED}", unpark, text)


def render(text: str) -> str:
    """Return the answer as safe HTML.

    Blocks are separated by blank lines, as in Markdown; a single newline inside
    a paragraph is a line break, because the school-database answers are written
    one fact per line and depend on it.
    """
    lines = (
        text.replace("\r\n", "\n").replace("\r", "\n").replace(_PARKED, "").split("\n")
    )
    out: list[str] = []
    paragraph: list[str] = []
    open_list: str | None = None

    def flush_paragraph() -> None:
        if paragraph:
            out.append("<p>" + "<br>".join(paragraph) + "</p>")
            paragraph.clear()

    def close_list() -> None:
        nonlocal open_list
        if open_list is not None:
            out.append(f"</{open_list}>")
            open_list = None

    def start_list(kind: str, item: str) -> None:
        nonlocal open_list
        flush_paragraph()
        if open_list != kind:
            close_list()
            out.append(f"<{kind}>")
            open_list = kind
        out.append(f"<li>{_inline(item)}</li>")

    for raw in lines:
        line = raw.strip()

        if not line:
            flush_paragraph()
            close_list()
            continue

        heading = _HEADING.match(line)
        if heading:
            flush_paragraph()
            close_list()
            out.append(f"<{_HEADING_TAG}>{_inline(heading.group(1))}</{_HEADING_TAG}>")
            continue

        bullet = _BULLET.match(line)
        if bullet:
            start_list("ul", bullet.group(1))
            continue

        ordered = _ORDERED.match(line)
        if ordered:
            start_list("ol", ordered.group(1))
            continue

        # Any other line ends a list: a paragraph in the middle of one would
        # otherwise be swallowed into the last item.
        close_list()
        paragraph.append(_inline(line))

    flush_paragraph()
    close_list()
    return "\n".join(out)
