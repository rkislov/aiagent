import json

import pytest

from superagent.audit.logger import Audit


class Scripted:
    def __init__(self, responses):
        self.responses = iter(responses)

    def chat(self, messages, tools):
        return next(self.responses)


def call(name, payload):
    return {"role": "assistant", "content": None, "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": name, "arguments": json.dumps(payload)}}]}


def test_demo_roundtrip_persists_and_audits(platform):
    events = list(platform.runner.run("office_assistant", "Счёт на оплату", "test_session"))
    assert events[-1]["type"] == "done"
    assert "Финансы" in events[-1]["content"]
    assert [e["status"] for e in events if e["type"] == "skill_result"] == ["ok", "ok"]
    history = platform.runner.memory(platform.registry.build("office_assistant")).load("test_session")
    assert len(history) == 6
    assert len(platform.audit.query()) == 5
    assert len((platform.root / "data/audit.log").read_text().splitlines()) == 5


@pytest.mark.parametrize("approved", [False, True, None])
def test_confirmation_gate(platform, approved):
    provider = Scripted([call("save_report", {"report": "hello"}), {"role": "assistant", "content": "done"}])
    confirm = (lambda approval: approved) if approved is not None else None
    events = list(platform.runner.run("office_assistant", "save", confirm=confirm, provider=provider))
    result = next(e for e in events if e["type"] == "skill_result")
    assert result["status"] == ("ok" if approved else "denied")
    assert (platform.root / "data/workspace/report.txt").exists() == bool(approved)


def test_max_steps_and_missing_skill(platform):
    platform.registry.agents["office_assistant"].max_steps = 1
    events = list(platform.runner.run("office_assistant", "test", provider=Scripted([call("unattached", {})])))
    assert next(e for e in events if e["type"] == "skill_result")["status"] == "error"
    assert "max_steps=1" in events[-1]["error"]


def test_agent_confirmation_applies_to_pure_skill(platform):
    platform.registry.agents["office_assistant"].confirmation_required.append("email_filter")
    events = list(platform.runner.run("office_assistant", "test"))
    assert any(e["type"] == "confirmation" and e["skill"] == "email_filter" for e in events)
    assert next(e for e in events if e["type"] == "skill_result")["status"] == "denied"


def test_audit_redaction_filters_and_rotation(tmp_path):
    audit = Audit(tmp_path, ["super-secret"], max_bytes=1024, backups=2)
    try:
        for _ in range(12):
            audit.record(agent="agent", skill="skill", kind="skill", input={"api_key": "oops", "text": "super-secret" * 40}, output="super-secret", status="denied")
        for path in (tmp_path / "data").glob("audit.log*"):
            assert "super-secret" not in path.read_text()
            assert "oops" not in path.read_text()
        assert (tmp_path / "data/audit.log.1").exists()
        assert len(audit.query(agent="agent", skill="skill", status="denied")) == 12
        assert audit.query(agent="other") == []
    finally:
        audit.close()


def test_approval_executes_snapshot(platform):
    def confirm(approval):
        (platform.root / "skills/save_report/skill.py").write_text('def run(input):\n    raise RuntimeError("changed after approval")')
        return True
    events = list(platform.runner.run("office_assistant", "сохрани отчёт", confirm=confirm))
    assert next(e for e in events if e["type"] == "skill_result")["status"] == "ok"


def test_resume_interrupted_tool_call(platform):
    memory = platform.runner.memory(platform.registry.build("office_assistant"))
    memory.ensure("interrupted", "office_assistant", "Previous request")
    memory.append("interrupted", {"role": "user", "content": "save"})
    memory.append("interrupted", call("save_report", {"report": "old"}))
    events = list(platform.runner.run("office_assistant", "new request", "interrupted"))
    assert events[-1]["type"] == "done"
    history = memory.load("interrupted")
    assert history[2]["role"] == "tool"
    assert "interrupted" in history[2]["content"]
    assert not (platform.root / "data/workspace/report.txt").exists()


def test_context_keeps_whole_turns(platform):
    platform.config.context_turns = 1
    list(platform.runner.run("office_assistant", "first", "context"))
    list(platform.runner.run("office_assistant", "second", "context"))
    class Inspect:
        def chat(self, messages, tools):
            users = [m["content"] for m in messages if m["role"] == "user"]
            assert users == ["second", "third"]
            calls = [c["id"] for m in messages for c in m.get("tool_calls", [])]
            results = [m["tool_call_id"] for m in messages if m["role"] == "tool"]
            assert calls == results
            return {"role": "assistant", "content": "ready"}
    list(platform.runner.run("office_assistant", "third", "context", provider=Inspect()))
