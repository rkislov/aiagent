import pytest

from superagent.gateway.models import Agent, Skill, validate_payload
from superagent.gateway.registry import parse_skill


def test_examples_build(platform):
    assert not platform.registry.errors
    from superagent.integrations.catalog import BUILTINS
    assert set(platform.registry.skills) == {"email_filter", "report_gen", "save_report"} | set(BUILTINS)
    assert platform.registry.build("office_assistant").max_steps == 10


def test_skill_validation(platform):
    with pytest.raises(ValueError):
        parse_skill("name: ../escape\ndescription: bad")
    with pytest.raises(ValueError):
        Skill(name="bad", description="bad", inputs={"text": "tuple"})
    with pytest.raises(ValueError):
        Skill(name="bad", description="bad", permissions={"network": ["*:*”"]})
    with pytest.raises(ValueError):
        validate_payload({"number": True}, {"number": "int"})
    with pytest.raises(ValueError):
        validate_payload({"messages": [1]}, {"messages": "list[dict]"})


def test_build_missing_and_incompatible(platform):
    agent = platform.registry.agents["office_assistant"].model_copy(deep=True)
    agent.skills.append("missing")
    with pytest.raises(ValueError, match="Missing"):
        platform.registry.build(agent.name, agent)
    agent.skills.remove("missing")
    platform.registry.skills["report_gen"].inputs["classified"] = "str"
    with pytest.raises(ValueError, match="Incompatible"):
        platform.registry.build(agent.name, agent)


def test_registration_reports_errors_and_hot_reload(platform):
    folder = platform.root / "skills/broken"
    folder.mkdir()
    (folder / "SKILL.md").write_text("name: broken\ndescription: invalid")
    platform.registry.scan()
    assert "skill:broken" in platform.registry.errors
    platform.registry.save_skill("echo", "name: echo\ndescription: Echo\ninputs: {text: str}\noutputs: {result: str}", 'def run(input):\n    return {"result": input["text"]}')
    assert "echo" in platform.registry.skills


def test_path_escape_and_symlink_rejected(platform, tmp_path):
    with pytest.raises(ValueError):
        platform.registry.path("skills", "../../escape")
    folder = platform.root / "skills/escape"
    folder.symlink_to(platform.root.parent, target_is_directory=True)
    with pytest.raises(ValueError, match="escapes"):
        platform.registry.path("skills", "escape")
