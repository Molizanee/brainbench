"""Vendored Anatomy skill at a pinned commit (design D11 step 4).

The pinned commit is fetched into a staging directory, verified with ``git rev-parse HEAD``,
stamped with ``.commit``, and swapped into place by rename. Any failure leaves the installed skill
untouched.
"""

import logging
import re
import shutil
import subprocess
from enum import StrEnum
from pathlib import Path

log = logging.getLogger(__name__)

COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
GIT_TIMEOUT_S = 300


class SkillInstallError(RuntimeError):
    pass


class SkillInstallResult(StrEnum):
    UNCHANGED = "unchanged"
    INSTALLED = "installed"


def read_pin(pin_file: Path) -> str:
    pin = pin_file.read_text().strip()
    if not COMMIT_RE.match(pin):
        raise SkillInstallError(f"{pin_file} does not contain a full 40-character commit hash")
    return pin


def installed_commit(target: Path) -> str | None:
    commit_file = target / ".commit"
    return commit_file.read_text().strip() if commit_file.is_file() else None


def _git(*args: str | Path) -> str:
    result = subprocess.run(
        ["git", *map(str, args)],
        check=False,
        capture_output=True,
        text=True,
        timeout=GIT_TIMEOUT_S,
    )
    if result.returncode != 0:
        raise SkillInstallError(f"git {args[0]} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def install_pinned_skill(target: Path, pin: str, repo_url: str) -> SkillInstallResult:
    staging = target.with_name(f"{target.name}.staging")
    old = target.with_name(f"{target.name}.old")

    # Recover from an interrupted swap: the previous skill is in .old and nothing is in place.
    if not target.exists() and old.exists():
        old.rename(target)

    if installed_commit(target) == pin:
        return SkillInstallResult.UNCHANGED
    if not COMMIT_RE.match(pin):
        raise SkillInstallError(f"invalid pin {pin!r}")

    shutil.rmtree(staging, ignore_errors=True)
    shutil.rmtree(old, ignore_errors=True)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        _git("init", "-q", staging)
        _git("-C", staging, "fetch", "-q", "--depth", "1", repo_url, pin)
        _git("-C", staging, "checkout", "-q", "--detach", "FETCH_HEAD")
        head = _git("-C", staging, "rev-parse", "HEAD")
        if head != pin:
            raise SkillInstallError(f"fetched commit {head} does not match pin {pin}")
        shutil.rmtree(staging / ".git")
        (staging / ".commit").write_text(pin + "\n")
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    if target.exists():
        target.rename(old)
    staging.rename(target)
    shutil.rmtree(old, ignore_errors=True)
    return SkillInstallResult.INSTALLED
