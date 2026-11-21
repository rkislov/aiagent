import json
import socket
import threading
import time

import httpx
import pytest
import uvicorn
from fastapi.testclient import TestClient

from superagent.web.app import create_app


@pytest.fixture
def client(platform):
    with TestClient(create_app(platform, authentication=False), base_url="http://localhost") as client:
        yield client


def test_workspace_and_security(client):
    assert client.get("/").status_code == 200
    workspace = client.get("/api/workspace").json()
    from superagent.integrations.catalog import BUILTINS
    assert len(workspace["skills"]) == 3 + len(BUILTINS)
    assert "api_key" not in json.dumps(workspace)
    assert client.get("/api/workspace", headers={"host": "evil.test"}).status_code == 400
    assert client.post("/api/chat", headers={"origin": "https://evil.test"}, json={"agent": "office_assistant", "message": "hello"}).status_code == 403
    assert client.post("/api/chat", content="anything", headers={"content-type": "text/plain"}).status_code == 415


def test_skill_editor_and_agent_builder(client):
    draft = {"name": "echo", "metadata": "name: echo\ndescription: Echo\ninputs: {text: str}\noutputs: {result: str}", "code": 'def run(input):\n    return {"result": input["text"].upper()}'}
    assert client.put("/api/skills/echo", json=draft).status_code == 200
    assert client.get("/api/skills/echo").json() == draft
    result = client.post("/api/skills/test", json={**draft, "input": {"text": "hello"}}).json()
    assert result == {"status": "ok", "output": {"result": "HELLO"}}
    agent = {"name": "echo_agent", "model": "demo:office", "instructions": "Echo", "skills": ["echo"]}
    assert client.put("/api/agents/echo_agent", json=agent).status_code == 200
    assert client.post("/api/agents/echo_agent/build", json={}).json()["ok"] is True
    agent["skills"] = ["missing"]
    assert client.put("/api/agents/echo_agent", json=agent).status_code == 400


def test_stream_and_saved_sessions(client):
    response = client.post("/api/chat", json={"agent": "office_assistant", "message": "Счёт на оплату"})
    frames = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
    assert frames[-1]["type"] == "done"
    assert any(e["type"] == "delta" for e in frames)
    sessions = client.get("/api/sessions/office_assistant").json()
    history = client.get("/api/sessions/office_assistant/" + sessions[0]["id"]).json()
    assert history[-1]["role"] == "assistant"
    assert len(client.get("/api/audit?status=ok").json()) == 5


def test_skill_test_requires_confirmation(client):
    draft = client.get("/api/skills/save_report").json()
    response = client.post("/api/skills/test", json={**draft, "input": {"report": "test"}})
    assert response.status_code == 409


@pytest.mark.parametrize("approved", [False, True])
def test_live_stream_confirmation(platform, approved):
    app = create_app(platform, authentication=False)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while not server.started and time.monotonic() < deadline:
        time.sleep(.01)
    assert server.started
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=10, trust_env=False) as client:
            events = []
            with client.stream("POST", "/api/chat", json={"agent": "office_assistant", "message": "Сохрани отчёт"}) as response:
                for line in response.iter_lines():
                    if not line.startswith("data: "):
                        continue
                    event = json.loads(line[6:])
                    events.append(event)
                    if event["type"] == "confirmation":
                        assert not (platform.root / "data/workspace/report.txt").exists()
                        endpoint = "/api/confirmations/" + event["id"]
                        assert client.post(endpoint, json={"approved": approved}).status_code == 200
                        assert client.post(endpoint, json={"approved": approved}).status_code == 404
            result = next(e for e in events if e["type"] == "skill_result")
            assert result["status"] == ("ok" if approved else "denied")
            assert (platform.root / "data/workspace/report.txt").exists() == approved
    finally:
        server.should_exit = True
        thread.join(5)
        sock.close()
