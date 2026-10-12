import asyncio
import os
import subprocess
from pathlib import Path
from typing import Any

import pymupdf as _pymupdf
import pytest
import trafilatura.downloads

from app.services.extraction import (
    ExtractionError,
    Section,
    clone_repository,
    collect_repository,
    extract_note,
    extract_pdf,
    extract_url,
    read_text_file,
    text_fragment,
)

# pymupdf ships no annotations for most of its API.
pymupdf: Any = _pymupdf


def make_pdf(path: Path, pages: list[str]) -> Path:
    document = pymupdf.Document()
    for text in pages:
        page = document.new_page()
        if text:
            page.insert_textbox(pymupdf.Rect(72, 72, 540, 770), text, fontsize=10)
    document.save(path)
    document.close()
    return path


# --- PDF ----------------------------------------------------------------------------------


def test_pdf_sections_carry_page_numbers(tmp_path: Path) -> None:
    pdf = make_pdf(
        tmp_path / "a.pdf",
        ["Raft elects a leader per term.", "", "Followers replicate the leader log."],
    )
    extracted = extract_pdf(pdf)
    assert extracted.sections == [
        Section("Raft elects a leader per term.", {"page": 1}),
        Section("Followers replicate the leader log.", {"page": 3}),
    ]


def test_pdf_without_text_fails(tmp_path: Path) -> None:
    pdf = make_pdf(tmp_path / "scan.pdf", ["", ""])
    with pytest.raises(ExtractionError, match="no extractable text"):
        extract_pdf(pdf)


def test_corrupt_pdf_fails(tmp_path: Path) -> None:
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"%PDF-1.7\nthis is not a pdf")
    with pytest.raises(ExtractionError, match=r"could not be opened|no extractable text"):
        extract_pdf(broken)


def test_encrypted_pdf_fails(tmp_path: Path) -> None:
    document = pymupdf.Document()
    document.new_page().insert_text((72, 72), "secret")
    path = tmp_path / "locked.pdf"
    document.save(path, encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="owner", user_pw="user")
    with pytest.raises(ExtractionError, match="password-protected"):
        extract_pdf(path)


# --- URL ----------------------------------------------------------------------------------


class FakeResponse:
    def __init__(self, status: int, html: str | None) -> None:
        self.status = status
        self.html = html


PAGE = """<html><head><title>Raft explained</title></head><body><article>
<h1>Raft explained</h1>
<p>Raft is a consensus algorithm that is designed to be easy to understand. It is equivalent
to Paxos in fault-tolerance and performance.</p>
<p>The difference is that it is decomposed into relatively independent subproblems, and it
cleanly addresses all major pieces needed for practical systems.</p>
</article></body></html>"""


def fake_fetch(response: FakeResponse | None) -> Any:
    def fetch(url: str, **kwargs: Any) -> FakeResponse | None:
        assert kwargs["config"].get("DEFAULT", "DOWNLOAD_TIMEOUT") == "12"
        return response

    return fetch


def test_url_extraction(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        trafilatura.downloads, "fetch_response", fake_fetch(FakeResponse(200, PAGE))
    )
    extracted = extract_url("https://raft.github.io/", timeout_s=12)
    assert extracted.title == "Raft explained"
    [section] = extracted.sections
    assert section.text_fragments is True
    assert "consensus algorithm" in section.text
    assert "major pieces" in section.text


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (None, "Could not download"),
        (FakeResponse(404, ""), "HTTP status 404"),
        (FakeResponse(200, None), "empty or undecodable"),
        (FakeResponse(200, "<html><body></body></html>"), "No readable text"),
    ],
)
def test_url_failures(
    monkeypatch: pytest.MonkeyPatch, response: FakeResponse | None, message: str
) -> None:
    monkeypatch.setattr(trafilatura.downloads, "fetch_response", fake_fetch(response))
    with pytest.raises(ExtractionError, match=message):
        extract_url("https://example.com/x", timeout_s=12)


def test_text_fragment() -> None:
    assert text_fragment("Raft elects a leader.") == "#:~:text=Raft%20elects%20a%20leader."
    long = "Leader-election uses randomized timeouts, so split votes are rare and resolve fast."
    assert text_fragment(long) == (
        "#:~:text=Leader%2Delection%20uses%20randomized%20timeouts%2C,rare%20and%20resolve%20fast."
    )


# --- Git ----------------------------------------------------------------------------------


def test_repository_files_are_filtered_and_ordered(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    files = {
        "README.md": "# Raft\nA library.",
        "docs/guide.md": "Guide.",
        "docs/README.rst": "Docs index.",
        "src/raft.go": "package raft",
        "src/b.py": "print('b')",
        "Makefile": "all:\n\ttrue",
        "logo.png": "not really an image",
        "package-lock.json": "{}",
        "node_modules/dep/index.js": "module.exports = 1",
        ".git/config": "[core]",
        "empty.md": "   ",
    }
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    (root / "src/blob.py").write_bytes(b"x = 1\0\0binary")
    (root / "src/latin1.py").write_bytes("caf\xe9".encode("latin-1"))
    (root / "src/huge.py").write_text("x" * 2048)
    os.symlink("/etc/hosts", root / "hosts.md")

    extracted = collect_repository(root, max_file_bytes=1024)
    assert [s.location["path"] for s in extracted.sections] == [
        "README.md",
        "docs/README.rst",
        "docs/guide.md",
        "Makefile",
        "src/b.py",
        "src/raft.go",
    ]
    assert extracted.sections[0].text == "# Raft\nA library."


def test_repository_without_wanted_files_fails(tmp_path: Path) -> None:
    (tmp_path / "data.bin").write_bytes(b"\0")
    with pytest.raises(ExtractionError, match="no README"):
        collect_repository(tmp_path, max_file_bytes=1024)


def test_read_text_file_limits(tmp_path: Path) -> None:
    path = tmp_path / "a.txt"
    path.write_text("x" * 10)
    assert read_text_file(path, 10) == "x" * 10
    assert read_text_file(path, 9) is None


@pytest.fixture
def bare_repo(tmp_path: Path) -> Path:
    origin = tmp_path / "origin"
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
    return origin


async def test_clone_refuses_non_http_transports(tmp_path: Path, bare_repo: Path) -> None:
    origin = bare_repo
    with pytest.raises(ExtractionError, match="Cloning file://"):
        await clone_repository(f"file://{origin}", tmp_path / "clone", timeout_s=30)
    assert not (tmp_path / "clone").exists()


async def test_clone_timeout_is_reported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real = asyncio.create_subprocess_exec

    async def slow(*args: Any, **kwargs: Any) -> Any:
        return await real("sleep", "5", **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", slow)
    with pytest.raises(ExtractionError, match="timed out after 0 s"):
        await clone_repository("https://example.com/r.git", tmp_path / "c", timeout_s=0.2)


# --- Note ---------------------------------------------------------------------------------


def test_note() -> None:
    assert extract_note("Some text").sections == [Section("Some text")]
    with pytest.raises(ExtractionError):
        extract_note("  ")
