import json
import os
import sys
import socket
import threading

import pytest

from superagent.gateway.models import Skill
from superagent.sandbox.executor import Executor
from superagent.sandbox.policy import SandboxError, resolve_permissions


def skill(**kwargs):
    return Skill(name="test_skill", description="Test", outputs={"result": "any"}, **kwargs)


def test_real_execution(platform):
    result = platform.runner.executor.run(platform.registry.skills["email_filter"], {"messages": [{"subject": "Invoice urgent"}]})
    assert result["classified"][0]["category"] == "Финансы"


@pytest.mark.parametrize("operation", [
    'open(SECRET).read()',
    '__import__("os").listdir(SECRET_PARENT)',
    '__import__("os").unlink(SECRET)',
    '__import__("socket").create_connection(("127.0.0.1", 9), timeout=1)',
    '__import__("socket").socket(2, 2).sendto(b"x", ("127.0.0.1", 9))',
    '__import__("subprocess").run(["/usr/bin/true"])',
    '__import__("ctypes").CDLL(None)',
])
def test_default_deny(platform, operation):
    secret = platform.root / "config.yaml"
    secret.write_text("DO_NOT_READ")
    expr = operation.replace("SECRET_PARENT", repr(str(platform.root))).replace("SECRET", repr(str(secret)))
    with pytest.raises(SandboxError):
        platform.runner.executor.run(skill(), {}, f'def run(input):\n    return {{"result": {expr}}}')
    assert secret.read_text() == "DO_NOT_READ"


def test_env_clean_and_no_user_packages(platform, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "secret-from-parent")
    result = platform.runner.executor.run(skill(), {}, 'import os,sys\ndef run(input):\n    return {"result": {"key": os.environ.get("OPENAI_API_KEY"), "site": any("site-packages" in p for p in sys.path)}}')
    assert result == {"result": {"key": None, "site": False}}


def test_granted_write_and_symlink_escape(platform):
    allowed = platform.root / "data/workspace"
    secret = platform.root / "config.yaml"
    secret.write_text("hidden")
    (allowed / "escape").symlink_to(secret)
    granted = skill(permissions={"filesystem": [str(allowed)]})
    output = platform.runner.executor.run(granted, {}, 'from pathlib import Path\nfrom skill_context import filesystem\ndef run(input):\n    p=Path(filesystem[0])/"test.txt"\n    p.write_text("ok")\n    return {"result": p.read_text()}')
    assert output["result"] == "ok"
    with pytest.raises(SandboxError, match="denied"):
        platform.runner.executor.run(granted, {}, 'from pathlib import Path\nfrom skill_context import filesystem\ndef run(input):\n    return {"result": (Path(filesystem[0])/"escape").read_text()}')


@pytest.mark.parametrize("path", [".", "data", "data/memory.db", "data/another.db", "config.yaml", "skills", "superagent"])
def test_protected_paths(platform, path):
    with pytest.raises(ValueError):
        resolve_permissions(platform.root, skill(permissions={"filesystem": [path]}).permissions)


def test_timeout_and_output_validation(platform):
    executor = Executor(platform.root, timeout=.25)
    with pytest.raises(SandboxError, match="timed out"):
        executor.run(skill(), {}, 'def run(input):\n    while True: pass')
    with pytest.raises(ValueError, match="Expected fields"):
        platform.runner.executor.run(skill(), {}, 'def run(input):\n    return {"wrong": 3}')


def test_fails_closed_without_backend(platform, monkeypatch):
    monkeypatch.setattr("superagent.sandbox.policy.shutil.which", lambda _: None)
    with pytest.raises(SandboxError, match="refusing unisolated"):
        platform.runner.executor.run(skill(), {}, 'def run(input):\n    return {"result": 1}')


@pytest.mark.skipif(sys.platform != "darwin", reason="Per-host network policy currently supported on macOS")
def test_explicit_network_grant(platform):
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    listener.settimeout(3)
    port = listener.getsockname()[1]
    def serve():
        try:
            connection, _ = listener.accept()
            with connection:
                connection.sendall(b"allowed")
        finally:
            listener.close()
    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    granted = skill(permissions={"network": [f"localhost:{port}"]})
    # Explicit IPv4 matches the policy even if localhost also resolves to IPv6.
    code = f'import socket\ndef run(input):\n    with socket.create_connection(("127.0.0.1", {port}), timeout=2) as s:\n        return {{"result": s.recv(16).decode()}}'
    assert platform.runner.executor.run(granted, {}, code)["result"] == "allowed"
    thread.join(3)


def test_explicit_subprocess_grant(platform):
    granted = skill(permissions={"subprocess": True})
    code = 'import subprocess\ndef run(input):\n    return {"result": subprocess.run(["/usr/bin/true"]).returncode}'
    assert platform.runner.executor.run(granted, {}, code) == {"result": 0}
