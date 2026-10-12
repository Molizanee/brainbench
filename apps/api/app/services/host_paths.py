"""Host path of the data directory, needed to bind-mount job directories into sibling sandbox
containers (design D7). The Docker daemon resolves ``-v`` sources on the host, not in the worker.
"""

import logging
import socket
import uuid
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


class HostPathError(RuntimeError):
    pass


class SandboxProbeError(RuntimeError):
    pass


def resolve_host_data_dir(client: Any, data_dir: Path, override: Path | None) -> Path:
    """Return the host path mounted at ``data_dir``.

    ``override`` (``BRAINBENCH_DATA_HOST``) wins when set; otherwise the worker inspects its own
    container through the Docker socket and reads the ``Source`` of the ``data_dir`` mount.
    """
    if override is not None:
        log.info("host data dir from BRAINBENCH_DATA_HOST: %s", override)
        return override
    hostname = socket.gethostname()
    try:
        mounts = client.containers.get(hostname).attrs.get("Mounts", [])
    except Exception as exc:
        raise HostPathError(
            f"cannot inspect this container ({hostname}) through the Docker socket: {exc}; "
            f"set BRAINBENCH_DATA_HOST to the host path mounted at {data_dir}"
        ) from exc
    for mount in mounts:
        if mount.get("Destination") == str(data_dir) and mount.get("Source"):
            return Path(mount["Source"])
    raise HostPathError(
        f"no bind mount found for {data_dir} on this container; "
        f"set BRAINBENCH_DATA_HOST to the host path mounted at {data_dir}"
    )


def to_host(path: Path, data_dir: Path, host_data_dir: Path) -> Path:
    """Translate a path under ``data_dir`` (inside the worker) to its host path."""
    try:
        relative = path.resolve().relative_to(data_dir.resolve())
    except ValueError as exc:
        raise HostPathError(f"{path} is not under {data_dir}") from exc
    return host_data_dir / relative


def run_sandbox_probe(client: Any, image: str, data_dir: Path, host_data_dir: Path) -> None:
    """Write a marker under ``jobs/.probe`` and require a throwaway sandbox to read it back
    through the resolved host path."""
    probe_dir = data_dir / "jobs" / ".probe"
    probe_dir.mkdir(parents=True, exist_ok=True)
    marker = probe_dir / "marker"
    token = uuid.uuid4().hex
    marker.write_text(token)
    try:
        output = client.containers.run(
            image,
            ["cat", "/probe/marker"],
            network_mode="none",
            read_only=True,
            remove=True,
            volumes={
                str(to_host(probe_dir, data_dir, host_data_dir)): {"bind": "/probe", "mode": "ro"}
            },
        )
    except Exception as exc:
        raise SandboxProbeError(
            f"sandbox probe failed with image {image} and host data dir {host_data_dir}: {exc}; "
            "check BRAINBENCH_DATA_HOST and that the sandbox images were built"
        ) from exc
    finally:
        marker.unlink(missing_ok=True)
    seen = output.decode().strip() if isinstance(output, bytes) else str(output).strip()
    if seen != token:
        raise SandboxProbeError(
            f"sandbox probe read {seen!r} instead of the marker through {host_data_dir}; "
            "the host data path is wrong, set BRAINBENCH_DATA_HOST"
        )
