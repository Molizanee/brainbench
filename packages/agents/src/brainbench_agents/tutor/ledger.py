"""Which passages the tutor was shown, and which of them its reply cites."""

import re
from collections.abc import Iterable

from brainbench_agents.tutor.citations import BRACKETS, citation_label, normalize
from brainbench_agents.tutor.context import ChunkRef

_DIGITS = re.compile(r"\d+")


class CitationLedger:
    """Collects every retrieved passage of one reply and recognizes their labels in its text.

    A bracket in the reply counts as a citation when it equals a retrieved passage's label
    (ignoring case, spacing and quote style), or names a retrieved source's title followed by
    that passage's page number or path. Brackets naming anything else are not citations, so the
    reply can never cite a passage the tutor was not shown.
    """

    def __init__(self) -> None:
        self._refs: dict[str, ChunkRef] = {}
        self._by_label: dict[str, ChunkRef] = {}
        self._cited: dict[str, ChunkRef] = {}
        self.searches = 0

    def add(self, refs: Iterable[ChunkRef]) -> None:
        for ref in refs:
            self._refs.setdefault(ref.chunk_id, ref)
            self._by_label.setdefault(normalize(ref.label), ref)

    @property
    def retrieved(self) -> list[ChunkRef]:
        return list(self._refs.values())

    @property
    def cited(self) -> list[ChunkRef]:
        return list(self._cited.values())

    def scan(self, text: str) -> list[ChunkRef]:
        """Passages newly cited in ``text`` (the whole reply so far), in order of appearance."""
        found = []
        for match in BRACKETS.finditer(text):
            ref = self._match(match.group(0))
            if ref is not None and ref.chunk_id not in self._cited:
                self._cited[ref.chunk_id] = ref
                found.append(ref)
        return found

    def _match(self, bracket: str) -> ChunkRef | None:
        exact = self._by_label.get(normalize(bracket))
        if exact is not None:
            return exact
        inner = normalize(bracket[1:-1])
        fallback = None
        for ref in self._refs.values():
            title = normalize(citation_label(ref.source_title, {})[1:].rsplit(",", 1)[0])
            if not inner.startswith(title + ","):
                continue
            rest = inner[len(title) + 1 :].strip()
            page = ref.location.get("page")
            path = ref.location.get("path")
            if page is not None:
                if str(page) in _DIGITS.findall(rest):
                    return ref
            elif path:
                if normalize(str(path)) in rest:
                    return ref
            elif fallback is None:
                fallback = ref
        return fallback
