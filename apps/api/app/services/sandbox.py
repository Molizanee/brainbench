"""Sandbox runner for untrusted generated code (design D7, task 7.2).

The worker starts sibling containers through the host Docker socket, so every bind source is a
host path: :class:`SandboxRunner` translates in-container data paths with ``host_paths.to_host``
and refuses any mount other than ``jobs/<id>/work``, ``jobs/<id>/out`` and ``skills/anatomy``.
Every container runs with no network, CPU, memory and pids limits, a read-only root filesystem
with a ``/tmp`` tmpfs, all capabilities dropped, and a wall-clock timeout. A job registers a kill
hook for the container's lifetime so cancellation and worker shutdown kill it.
"""

import asyncio
import logging
import re
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.services.host_paths import resolve_host_data_dir, to_host

__lazy_modules__ = {"docker"}
import docker

log = logging.getLogger(__name__)

type KillHook = Callable[[], Awaitable[None]]
type RegisterKillHook = Callable[[KillHook], Callable[[], None]]

# The only data directories a sandbox may see (design D7 "Sandbox mounts").
ALLOWED_MOUNTS = re.compile(r"^(jobs/[0-9a-f]{8}-[0-9a-f-]{27}/(work|out)|skills/anatomy)$")
LOG_LIMIT_BYTES = 256 * 1024


class SandboxError(RuntimeError):
    """The sandbox could not be started (image missing, daemon unreachable, bad mount)."""


class MountNotAllowed(SandboxError):
    pass


@dataclass(frozen=True)
class Mount:
    """``path`` is inside the worker's data dir; it is bound at ``target`` in the sandbox."""

    path: Path
    target: str
    read_only: bool = True


@dataclass(frozen=True)
class SandboxLimits:
    timeout_s: float
    cpus: float = 2.0
    memory: str = "4g"
    pids: int = 256
    tmpfs_size: str = "1g"


@dataclass(frozen=True)
class SandboxResult:
    exit_code: int | None
    stdout: str
    stderr: str
    output: str  # stdout and stderr interleaved, as the container wrote them
    timed_out: bool
    killed: bool
    duration_s: float

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out and not self.killed


def mount_relative(path: Path, data_dir: Path) -> str:
    """``path`` relative to the data dir, when it is one of the allowed sandbox mounts."""
    try:
        relative = path.resolve().relative_to(data_dir.resolve()).as_posix()
    except ValueError as exc:
        raise MountNotAllowed(f"{path} is not under the data directory") from exc
    if not ALLOWED_MOUNTS.match(relative):
        raise MountNotAllowed(
            f"{relative} may not be mounted into a sandbox "
            "(only jobs/<id>/work, jobs/<id>/out and skills/anatomy)"
        )
    return relative


def _tail(raw: bytes | str) -> str:
    data = raw if isinstance(raw, bytes) else raw.encode()
    if len(data) > LOG_LIMIT_BYTES:
        data = b"[... output truncated ...]\n" + data[-LOG_LIMIT_BYTES:]
    return data.decode(errors="replace")


def _kill_quietly(container: Any) -> None:
    try:
        container.kill()
    except Exception as exc:  # already exited or removed
        log.debug("sandbox %s kill: %s", getattr(container, "id", "?"), exc)


def _remove_quietly(container: Any) -> None:
    try:
        container.remove(force=True)
    except Exception as exc:
        log.warning("sandbox %s not removed: %s", getattr(container, "id", "?"), exc)


_host_data_dirs: dict[Path, Path] = {}


class SandboxRunner:
    """Runs one command per container. ``client`` and ``host_data_dir`` are injectable for
    tests; by default the Docker client comes from the environment (the mounted socket) and the
    host data dir from ``BRAINBENCH_DATA_HOST`` or self-inspection, resolved once per process."""

    def __init__(
        self,
        *,
        client: Any = None,
        data_dir: Path | None = None,
        host_data_dir: Path | None = None,
    ) -> None:
        self._client = client
        self.data_dir = data_dir or settings.brainbench_data
        self._host_data_dir = host_data_dir

    @property
    def client(self) -> Any:
        if self._client is None:
            self._client = docker.from_env()
        return self._client

    def host_data_dir(self) -> Path:
        if self._host_data_dir is None:
            cached = _host_data_dirs.get(self.data_dir)
            if cached is None:
                cached = resolve_host_data_dir(
                    self.client, self.data_dir, settings.brainbench_data_host
                )
                _host_data_dirs[self.data_dir] = cached
            self._host_data_dir = cached
        return self._host_data_dir

    def volumes(self, mounts: Sequence[Mount]) -> dict[str, dict[str, str]]:
        """Docker ``volumes`` for ``mounts``, each checked against the allowed directories."""
        volumes = {}
        for mount in mounts:
            mount_relative(mount.path, self.data_dir)
            source = to_host(mount.path, self.data_dir, self.host_data_dir())
            volumes[str(source)] = {"bind": mount.target, "mode": "ro" if mount.read_only else "rw"}
        return volumes

    async def run(
        self,
        image: str,
        command: Sequence[str],
        *,
        mounts: Sequence[Mount],
        limits: SandboxLimits,
        environment: Mapping[str, str] | None = None,
        entrypoint: Sequence[str] | None = None,
        workdir: str | None = None,
        register_kill_hook: RegisterKillHook | None = None,
        label: str = "",
    ) -> SandboxResult:
        """Run ``command`` and wait for it, at most ``limits.timeout_s`` of wall-clock time.

        A timeout kills the container (``timed_out``); a kill hook firing kills it too
        (``killed``). The container is always removed. Raises :class:`SandboxError` when the
        container cannot be started.
        """
        volumes = self.volumes(mounts)
        options: dict[str, Any] = {
            "detach": True,
            "network_mode": "none",
            "nano_cpus": int(limits.cpus * 1e9),
            "mem_limit": limits.memory,
            "memswap_limit": limits.memory,
            "pids_limit": limits.pids,
            "read_only": True,
            "tmpfs": {"/tmp": f"rw,exec,nosuid,nodev,size={limits.tmpfs_size}"},
            "cap_drop": ["ALL"],
            "security_opt": ["no-new-privileges"],
            "volumes": volumes,
            "environment": dict(environment or {}),
            "labels": {"brainbench.sandbox": label},
        }
        if entrypoint is not None:
            options["entrypoint"] = list(entrypoint)
        if workdir is not None:
            options["working_dir"] = workdir
        try:
            container = await asyncio.to_thread(
                self.client.containers.run, image, list(command), **options
            )
        except Exception as exc:
            raise SandboxError(f"cannot start sandbox {image}: {exc}") from exc
        log.info("sandbox %s started (%s, %s)", container.id[:12], image, label)

        killed = False

        async def kill() -> None:
            nonlocal killed
            killed = True
            await asyncio.to_thread(_kill_quietly, container)

        unregister = register_kill_hook(kill) if register_kill_hook is not None else None
        started = time.monotonic()
        timed_out = False
        exit_code: int | None = None
        try:
            try:
                status = await asyncio.wait_for(
                    asyncio.to_thread(container.wait), timeout=limits.timeout_s
                )
                exit_code = status.get("StatusCode")
            except TimeoutError:
                timed_out = True
                log.warning(
                    "sandbox %s timed out after %.0f s", container.id[:12], limits.timeout_s
                )
                await asyncio.to_thread(_kill_quietly, container)
                await asyncio.to_thread(container.wait)
            duration = time.monotonic() - started
            stdout = await asyncio.to_thread(container.logs, stdout=True, stderr=False)
            stderr = await asyncio.to_thread(container.logs, stdout=False, stderr=True)
            output = await asyncio.to_thread(container.logs, stdout=True, stderr=True)
        except asyncio.CancelledError:
            _kill_quietly(container)
            raise
        finally:
            if unregister is not None:
                unregister()
            await asyncio.to_thread(_remove_quietly, container)
        result = SandboxResult(
            exit_code=None if timed_out or killed else exit_code,
            stdout=_tail(stdout),
            stderr=_tail(stderr),
            output=_tail(output),
            timed_out=timed_out,
            killed=killed and not timed_out,
            duration_s=duration,
        )
        log.info(
            "sandbox %s finished: exit %s, %.1f s%s",
            container.id[:12],
            result.exit_code,
            duration,
            " (timeout)" if timed_out else " (killed)" if result.killed else "",
        )
        return result


def prepare_dir(path: Path, *, writable: bool) -> Path:
    """Create a job directory before mounting it. Sandbox images run as uid 1000 while the
    worker runs as root, so a directory the sandbox writes is made world-writable."""
    path.mkdir(parents=True, exist_ok=True)
    path.chmod(0o777 if writable else 0o755)
    return path
