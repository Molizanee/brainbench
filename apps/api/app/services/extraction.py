"""Text extraction per source type (design D5).

URL pages go through trafilatura, PDFs through pymupdf (one section per page), Git repositories
through a shallow clone filtered by an allowlist, and notes are used as written. Each extractor
returns ``Section``s whose ``location`` every chunk cut from that section inherits. Failures that
the engineer should read raise ``ExtractionError`` with a human-readable message.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote

__lazy_modules__ = {"pymupdf", "trafilatura", "trafilatura.downloads", "trafilatura.settings"}
import pymupdf
import trafilatura
import trafilatura.downloads
import trafilatura.settings

log = logging.getLogger(__name__)

# Git: documentation and source files are ingested; everything else is skipped.
GIT_DOC_SUFFIXES = frozenset({".md", ".markdown", ".mdx", ".rst", ".txt", ".adoc", ".asciidoc"})
GIT_SOURCE_SUFFIXES = frozenset(
    {
        ".py", ".pyi", ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".go", ".rs", ".java",
        ".kt", ".kts", ".scala", ".c", ".h", ".cc", ".cpp", ".cxx", ".hpp", ".hh", ".cs",
        ".fs", ".swift", ".m", ".rb", ".php", ".ex", ".exs", ".erl", ".hs", ".ml", ".clj",
        ".lua", ".r", ".jl", ".dart", ".zig", ".sh", ".bash", ".sql", ".proto", ".graphql",
        ".tf", ".vue", ".svelte", ".yaml", ".yml", ".toml",
    }
)  # fmt: skip
GIT_SOURCE_NAMES = frozenset({"Makefile", "Dockerfile", "Justfile", "justfile"})
GIT_SKIP_DIRS = frozenset(
    {".git", "node_modules", "vendor", "third_party", "dist", "build", "target", "__pycache__"}
)
BINARY_SNIFF_BYTES = 8192
FRAGMENT_WORDS = 4


class ExtractionError(Exception):
    """Extraction failed for a reason the engineer can act on; ``str()`` is shown in the UI."""


@dataclass(frozen=True)
class Section:
    text: str
    location: dict[str, Any] = field(default_factory=dict)
    # URL sections: each chunk gets a text fragment (``#:~:text=start,end``) pointing at itself.
    text_fragments: bool = False


@dataclass(frozen=True)
class Extracted:
    sections: list[Section]
    title: str | None = None


def _encode_fragment_part(words: list[str]) -> str:
    # Text fragment syntax reserves "-", "," and "&"; quote() leaves "-" alone.
    return quote(" ".join(words), safe="").replace("-", "%2D")


def text_fragment(text: str) -> str:
    """``#:~:text=`` directive that makes a browser scroll to and highlight ``text``."""
    # Markup-only tokens (table pipes, rules) are not rendered text and would break the match.
    words = [word for word in text.split() if any(c.isalnum() for c in word)]
    if len(words) <= 2 * FRAGMENT_WORDS:
        return "#:~:text=" + _encode_fragment_part(words)
    start = _encode_fragment_part(words[:FRAGMENT_WORDS])
    end = _encode_fragment_part(words[-FRAGMENT_WORDS:])
    return f"#:~:text={start},{end}"


# --- URL ----------------------------------------------------------------------------------


def extract_url(url: str, *, timeout_s: float) -> Extracted:
    config = trafilatura.settings.use_config()
    config.set("DEFAULT", "DOWNLOAD_TIMEOUT", str(int(timeout_s)))
    config.set("DEFAULT", "INSECURE_SSL_FALLBACK", "off")
    response = trafilatura.downloads.fetch_response(url, decode=True, config=config)
    if response is None:
        raise ExtractionError(f"Could not download {url}.")
    if response.status != 200:
        raise ExtractionError(f"Downloading {url} failed with HTTP status {response.status}.")
    if not response.html:
        raise ExtractionError(f"{url} returned an empty or undecodable page.")

    document = trafilatura.bare_extraction(
        response.html, url=url, with_metadata=True, include_comments=False, config=config
    )
    text = getattr(document, "text", None) or ""
    if not text.strip():
        raise ExtractionError(f"No readable text was found at {url}.")
    title = getattr(document, "title", None)
    return Extracted([Section(text, text_fragments=True)], title=title or None)


# --- PDF ----------------------------------------------------------------------------------


def extract_pdf(path: Path) -> Extracted:
    """One section per page with ``{"page": n}`` (1-based); blocks become paragraphs."""
    try:
        document = pymupdf.Document(path)  # type: ignore[no-untyped-call]
    except Exception as exc:
        raise ExtractionError(f"The PDF could not be opened: {exc}") from exc
    with document:
        if document.needs_pass:
            raise ExtractionError("The PDF is password-protected.")
        # Expand ligatures ("ﬁ" -> "fi") so chunks embed and match like the words they spell.
        flags = (
            pymupdf.TEXTFLAGS_TEXT & ~pymupdf.TEXT_PRESERVE_LIGATURES
        ) | pymupdf.TEXT_DEHYPHENATE
        sections = []
        for page in document.pages():
            blocks = page.get_text("blocks", flags=flags, sort=True)  # type: ignore[no-untyped-call]
            # Block tuples: (x0, y0, x1, y1, text, block_no, block_type); type 0 is text.
            paragraphs = [" ".join(b[4].split()) for b in blocks if b[6] == 0 and b[4].strip()]
            if paragraphs:
                sections.append(Section("\n\n".join(paragraphs), {"page": page.number + 1}))
    if not sections:
        raise ExtractionError(
            "The PDF contains no extractable text (scanned PDFs without a text layer are not "
            "supported)."
        )
    return Extracted(sections)


# --- Git ----------------------------------------------------------------------------------


async def clone_repository(url: str, target: Path, *, timeout_s: float) -> None:
    """Shallow clone over http(s) only, without prompts, credential helpers, or LFS blobs."""
    process = await asyncio.create_subprocess_exec(
        "git",
        "-c", "protocol.allow=never",
        "-c", "protocol.https.allow=always",
        "-c", "protocol.http.allow=always",
        "-c", "credential.helper=",
        "-c", "core.askPass=true",
        "-c", "filter.lfs.smudge=",
        "-c", "filter.lfs.process=",
        "-c", "filter.lfs.required=false",
        "clone", "--depth", "1", "--single-branch", "--no-tags", "--quiet",
        "--", url, str(target),
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )  # fmt: skip
    try:
        async with asyncio.timeout(timeout_s):
            _, stderr = await process.communicate()
    except TimeoutError:
        process.kill()
        await process.wait()
        raise ExtractionError(f"Cloning {url} timed out after {int(timeout_s)} s.") from None
    if process.returncode != 0:
        lines = [line for line in stderr.decode(errors="replace").splitlines() if line.strip()]
        reason = lines[-1].removeprefix("fatal: ") if lines else f"exit {process.returncode}"
        raise ExtractionError(f"Cloning {url} failed: {reason}")


def _git_rank(path: Path) -> tuple[int, str]:
    rel = path.as_posix()
    if path.stem.lower() == "readme":
        return (0 if len(path.parts) == 1 else 1, rel)
    if path.suffix.lower() in GIT_DOC_SUFFIXES:
        return (2, rel)
    return (3, rel)


def _wanted(path: Path) -> bool:
    return (
        path.stem.lower() == "readme"
        or path.suffix.lower() in GIT_DOC_SUFFIXES
        or path.suffix.lower() in GIT_SOURCE_SUFFIXES
        or path.name in GIT_SOURCE_NAMES
    )


def read_text_file(path: Path, max_bytes: int) -> str | None:
    """File content as text, or ``None`` when it is too large, binary, or not UTF-8."""
    if path.is_symlink() or not path.is_file() or path.stat().st_size > max_bytes:
        return None
    data = path.read_bytes()
    if b"\0" in data[:BINARY_SNIFF_BYTES]:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def collect_repository(root: Path, *, max_file_bytes: int) -> Extracted:
    """README first, then documentation, then source files, each with ``{"path": ...}``."""
    candidates: list[Path] = []
    for directory, dirnames, filenames in root.walk():
        dirnames[:] = sorted(d for d in dirnames if d not in GIT_SKIP_DIRS)
        candidates.extend(
            (directory / name).relative_to(root) for name in filenames if _wanted(Path(name))
        )
    sections = []
    for rel in sorted(candidates, key=_git_rank):
        text = read_text_file(root / rel, max_file_bytes)
        if text is not None and text.strip():
            sections.append(Section(text, {"path": rel.as_posix()}))
    if not sections:
        raise ExtractionError("The repository has no README, documentation, or source files.")
    return Extracted(sections)


async def extract_git(
    url: str, workdir: Path, *, timeout_s: float, max_file_bytes: int
) -> Extracted:
    target = workdir / "repo"
    await clone_repository(url, target, timeout_s=timeout_s)
    return await asyncio.to_thread(collect_repository, target, max_file_bytes=max_file_bytes)


# --- Note ---------------------------------------------------------------------------------


def extract_note(text: str) -> Extracted:
    if not text.strip():
        raise ExtractionError("The note is empty.")
    return Extracted([Section(text)])
