"""The citation contract: ``[source-title, location]`` (design D10).

Every retrieved passage is shown to the model under its exact label, and the model is told to
copy that label next to each claim it draws from the passage. The caller recognizes the labels
in the streamed reply (``CitationLedger`` in ``ledger.py``) and turns them into citation events,
so only passages that were actually retrieved can ever be cited.
"""

import re
from typing import Any
from urllib.parse import unquote

FRAGMENT_WORDS = 6

_QUOTES = str.maketrans(
    {"\u201c": '"', "\u201d": '"', "\u2018": "'", "\u2019": "'", "\u2026": "..."}
)


def fragment_start(fragment: str) -> str:
    """The first words a W3C text fragment (``#:~:text=prefix-,start,end,-suffix``) points at."""
    _, _, directive = fragment.partition(":~:text=")
    if not directive:
        return ""
    parts = [unquote(p) for p in directive.split("&")[0].split(",")]
    body = [p for p in parts if not p.endswith("-") and not p.startswith("-")]
    if not body:
        return ""
    return " ".join(body[0].split()[:FRAGMENT_WORDS])


def location_label(location: dict[str, Any]) -> str:
    """Human-readable location: ``p. 5`` for a PDF page, the path for a repository file, the
    passage's first words for a web page, ``note`` for a plain note."""
    if location.get("page") is not None:
        return f"p. {location['page']}"
    if location.get("path"):
        return str(location["path"])
    if location.get("fragment"):
        start = fragment_start(str(location["fragment"]))
        return f"“{start}…”" if start else "web page"
    return "note"


def citation_label(source_title: str, location: dict[str, Any]) -> str:
    title = " ".join(source_title.split()).replace("[", "(").replace("]", ")") or "Untitled"
    return f"[{title}, {location_label(location)}]"


def normalize(text: str) -> str:
    """Comparison form: case, whitespace, and typographic quotes do not matter."""
    return " ".join(text.translate(_QUOTES).casefold().split())


BRACKETS = re.compile(r"\[([^\[\]\n]{1,300})\]")
