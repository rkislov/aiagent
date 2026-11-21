"""OS isolation is mandatory; Python audit checks improve error messages only."""
from __future__ import annotations

import ipaddress
import json
import shutil
import socket
import subprocess
import sys
from functools import lru_cache
from pathlib import Path

from superagent.gateway.models import Permissions


class SandboxError(RuntimeError):
    pass


def runtime_libraries(runtime: Path) -> list[Path]:
    """Read grants for external shared libraries used by a Homebrew Python."""
    return list(_runtime_libraries(str(runtime)))


@lru_cache(maxsize=4)
def _runtime_libraries(runtime: str) -> tuple[Path, ...]:
    # otool only inspects installed interpreter binaries, never skill-controlled files.
    modules = list((Path(runtime) / "lib").glob("python*/lib-dynload/*.so"))
    if not modules:
        return ()
    result = subprocess.run(["/usr/bin/otool", "-L", *map(str, modules)], capture_output=True, text=True, timeout=10, check=True)
    paths = set()
    for line in result.stdout.splitlines():
        candidate = line.strip().split(" (", 1)[0]
        if line.startswith("\t") and candidate.startswith(("/opt/homebrew/", "/usr/local/")):
            path = Path(candidate).resolve()
            if path.exists():
                paths.add(path)
    return tuple(sorted(paths))


def resolve_permissions(root: Path, permissions: Permissions) -> list[Path]:
    protected = [root / "config.yaml", root / ".env", root / "agents", root / "skills",
                 root / "superagent", root / "platform", root / "data/memory.db", root / "data/audit.db", root / "data/audit.log"]
    paths = []
    for value in permissions.filesystem:
        if not value.strip():
            raise ValueError("Empty filesystem permission")
        path = (root / value).resolve()
        if (root / ".tenant").exists() and not path.is_relative_to((root / "data/workspace").resolve()):
            raise ValueError("User skills may only access their own data/workspace")
        if path.is_relative_to((root / "data").resolve()) and not path.is_relative_to((root / "data/workspace").resolve()):
            raise ValueError("Inside data/, only data/workspace may be granted to skills")
        if any(path == p.resolve() or path.is_relative_to(p.resolve()) or p.resolve().is_relative_to(path) for p in protected):
            raise ValueError(f"Filesystem permission overlaps protected platform data: {value}")
        if path == Path.home() or path == Path("/"):
            raise ValueError("Broad home/root permissions are forbidden")
        paths.append(path)
    return paths


def network_endpoints(values: list[str]) -> list[tuple[str, int]]:
    endpoints = []
    for value in values:
        host, port = value.rsplit(":", 1)
        # Resolve before launch. The child cannot freely query DNS or bypass the IP allowlist.
        try:
            for item in socket.getaddrinfo(host.strip("[]"), int(port), type=socket.SOCK_STREAM):
                endpoints.append((str(ipaddress.ip_address(item[4][0])), int(port)))
        except OSError as exc:
            raise SandboxError(f"Cannot resolve allowed network host: {host}") from exc
    return sorted(set(endpoints))


def sandbox_command(worker: Path, stage: Path, paths: list[Path], relays: list[str], subprocess_allowed: bool) -> list[str]:
    python = str(Path(sys.executable).resolve())
    runtime = Path(sys.base_prefix).resolve()
    base = [python, "-I", "-S", "-B", str(worker)]
    if sys.platform == "darwin":
        if not shutil.which("sandbox-exec"):
            raise SandboxError("sandbox-exec is required on macOS; refusing unisolated execution")
        q = lambda value: json.dumps(str(value))
        reads = [runtime, Path("/System/Library"), Path("/usr/lib"), Path("/usr/share/zoneinfo"), stage]
        profile = ["(version 1)", "(deny default)", "(allow process-exec (literal " + q(python) + "))",
                   "(allow sysctl-read)", "(allow signal (target self))",
                   '(allow file-read* (literal "/"))',
                   '(allow file-read* file-write* (literal "/dev/null"))',
                   '(allow file-read* (literal "/dev/urandom") (literal "/dev/random"))']
        for path in reads:
            profile.append(f"(allow file-read* file-map-executable (subpath {q(path)}))")
        for library in runtime_libraries(runtime):
            profile.append(f"(allow file-read* file-map-executable (literal {q(library)}))")
        for certificates in ("/private/etc/ssl/cert.pem", "/private/etc/ssl/certs", "/opt/homebrew/etc/ca-certificates/cert.pem"):
            profile.append(f"(allow file-read* (subpath {q(certificates)}))")
        framework_binary = runtime / "Resources/Python.app/Contents/MacOS/Python"
        if framework_binary.exists():
            profile.append(f"(allow process-exec (literal {q(framework_binary)}))")
        # CPython inspects ancestor directories during startup.
        profile.append('(allow file-read-metadata)')
        for path in paths:
            profile.append(f"(allow file-read* file-write* (subpath {q(path)}))")
        for relay in relays:
            profile.append(f'(allow network-outbound (literal {q(relay)}))')
        if subprocess_allowed:
            profile.extend(['(allow process-fork)', '(allow process-exec)',
                            '(allow file-read* (subpath "/bin") (subpath "/usr/bin"))'])
        profile_path = stage / "policy.sb"
        profile_path.write_text("\n".join(profile))
        return ["/usr/bin/sandbox-exec", "-f", str(profile_path), *base]
    if sys.platform.startswith("linux"):
        if not shutil.which("bwrap"):
            raise SandboxError("Install bubblewrap (bwrap) for Linux sandboxing; refusing unisolated execution")
        cmd = ["bwrap", "--unshare-all", "--die-with-parent", "--new-session", "--proc", "/proc", "--dev", "/dev"]
        for path in dict.fromkeys([Path("/usr"), Path("/lib"), Path("/lib64"), runtime, stage]):
            if path.exists():
                cmd += ["--ro-bind", str(path), str(path)]
        for path in paths:
            if not path.exists():
                raise SandboxError(f"Create permitted path before execution: {path}")
            cmd += ["--bind", str(path), str(path)]
        cmd += ["--chdir", str(stage), "--", *base]
        return cmd
    raise SandboxError("No supported OS sandbox on this platform; execution denied")
