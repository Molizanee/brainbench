import subprocess
from pathlib import Path

import pytest

from app.services.skill_install import (
    SkillInstallError,
    SkillInstallResult,
    install_pinned_skill,
    installed_commit,
    read_pin,
)


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


@pytest.fixture
def upstream(tmp_path: Path) -> tuple[str, list[str]]:
    repo = tmp_path / "upstream"
    repo.mkdir()
    git(repo, "init", "-q")
    commits = []
    for version in ("one", "two"):
        (repo / "SKILL.md").write_text(f"skill {version}\n")
        git(repo, "add", ".")
        git(repo, "commit", "-q", "-m", version)
        commits.append(git(repo, "rev-parse", "HEAD"))
    return repo.as_uri(), commits


def test_fresh_install_then_unchanged(tmp_path: Path, upstream: tuple[str, list[str]]) -> None:
    url, (first, _) = upstream
    target = tmp_path / "skills" / "anatomy"

    assert install_pinned_skill(target, first, url) is SkillInstallResult.INSTALLED
    assert installed_commit(target) == first
    assert (target / "SKILL.md").read_text() == "skill one\n"
    assert not (target / ".git").exists()
    assert install_pinned_skill(target, first, url) is SkillInstallResult.UNCHANGED


def test_pin_change_swaps_atomically(tmp_path: Path, upstream: tuple[str, list[str]]) -> None:
    url, (first, second) = upstream
    target = tmp_path / "skills" / "anatomy"
    install_pinned_skill(target, first, url)

    assert install_pinned_skill(target, second, url) is SkillInstallResult.INSTALLED
    assert installed_commit(target) == second
    assert (target / "SKILL.md").read_text() == "skill two\n"
    assert sorted(p.name for p in target.parent.iterdir()) == ["anatomy"]


def test_bad_pin_leaves_previous_skill(tmp_path: Path, upstream: tuple[str, list[str]]) -> None:
    url, (first, _) = upstream
    target = tmp_path / "skills" / "anatomy"
    install_pinned_skill(target, first, url)

    with pytest.raises(SkillInstallError):
        install_pinned_skill(target, "0" * 40, url)
    assert installed_commit(target) == first
    assert sorted(p.name for p in target.parent.iterdir()) == ["anatomy"]


def test_interrupted_swap_recovers_previous_skill(
    tmp_path: Path, upstream: tuple[str, list[str]]
) -> None:
    url, (first, _) = upstream
    target = tmp_path / "skills" / "anatomy"
    install_pinned_skill(target, first, url)
    target.rename(target.with_name("anatomy.old"))

    assert install_pinned_skill(target, first, url) is SkillInstallResult.UNCHANGED
    assert installed_commit(target) == first


def test_read_pin_requires_full_hash(tmp_path: Path) -> None:
    pin_file = tmp_path / "anatomy.pin"
    pin_file.write_text("abc123\n")
    with pytest.raises(SkillInstallError):
        read_pin(pin_file)
    pin_file.write_text("a" * 40 + "\n")
    assert read_pin(pin_file) == "a" * 40


def test_repo_pin_file_is_valid() -> None:
    pin_file = Path(__file__).resolve().parents[4] / "infra" / "anatomy.pin"
    assert len(read_pin(pin_file)) == 40
