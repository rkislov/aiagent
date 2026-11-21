from __future__ import annotations

import json
import math
import os
import signal
import subprocess
import tempfile
from pathlib import Path

from superagent.gateway.models import Skill, validate_payload
from .policy import SandboxError, network_endpoints, resolve_permissions, sandbox_command
from .network import NetworkRelays


class Executor:
    def __init__(self, root: Path, timeout: float = 60):
        self.root, self.timeout = root, timeout

    def run(self, skill: Skill, payload: dict, code: str | None = None) -> dict:
        validate_payload(payload, skill.inputs)
        paths = resolve_permissions(self.root, skill.permissions)
        endpoints = network_endpoints(skill.permissions.network)
        hosts = {}
        import socket
        for value in skill.permissions.network:
            host, port = value.rsplit(":", 1)
            # Pin names only to addresses included in the OS policy.
            candidates = socket.getaddrinfo(host.strip("[]"), int(port), type=socket.SOCK_STREAM)
            match = next((item[4][0] for item in candidates if (item[4][0], int(port)) in endpoints), None)
            if match is None:
                raise SandboxError(f"Allowed hostname changed during resolution: {host}")
            hosts[value] = match
        with tempfile.TemporaryDirectory(prefix="sa-", dir="/tmp") as tmp:
            stage = Path(tmp).resolve()
            worker = stage / "worker.py"
            worker.write_bytes(Path(__file__).with_name("worker.py").read_bytes())
            source = stage / "skill.py"
            source.write_text(code if code is not None else (self.root / "skills" / skill.name / "skill.py").read_text())
            request = {"input": payload, "source": str(source), "paths": [str(p) for p in paths],
                       "endpoints": endpoints, "hosts": hosts, "subprocess": skill.permissions.subprocess,
                       "cpu_seconds": max(1, math.ceil(self.timeout))}
            # No inherited API keys, HOME, PYTHONPATH, or user site-packages.
            env = {"PATH": "/usr/bin:/bin", "LANG": "en_US.UTF-8", "TMPDIR": str(stage)}
            with NetworkRelays(stage, endpoints, self.timeout) as routes, tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
                request["routes"] = routes
                command = sandbox_command(worker, stage, paths, list(routes.values()), skill.permissions.subprocess)
                proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=output, stderr=errors,
                                        cwd=stage, env=env, start_new_session=True)
                try:
                    proc.communicate(json.dumps(request).encode(), timeout=self.timeout)
                except subprocess.TimeoutExpired as exc:
                    os.killpg(proc.pid, signal.SIGKILL)
                    proc.wait()
                    raise SandboxError(f"Skill timed out after {self.timeout:g} seconds") from exc
                finally:
                    # Also reap descendants created by skills with subprocess permission.
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                output.seek(0)
                raw = output.read(1_000_001)
                errors.seek(0)
                stderr = errors.read(4000).decode(errors="replace")
            try:
                result = json.loads(raw)
            except (ValueError, UnicodeDecodeError) as exc:
                raise SandboxError(f"Sandbox process failed ({proc.returncode}): {stderr or 'invalid or excessive output'}") from exc
            if not result.get("ok") or proc.returncode != 0:
                raise SandboxError(result.get("error", "Skill process failed"))
            validate_payload(result["output"], skill.outputs)
            return result["output"]
