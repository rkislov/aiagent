from __future__ import annotations

import ast
import os
import re
import tempfile
from pathlib import Path

import yaml

from superagent.integrations.catalog import BUILTINS
from .config import Config
from .models import NAME, Agent, Skill


def parse_skill(text: str) -> Skill:
    if text.startswith("---"):
        chunks = text.split("---", 2)
        if len(chunks) != 3:
            raise ValueError("Unclosed YAML front matter")
        text = chunks[1]
    return Skill.model_validate(yaml.safe_load(text))


def atomic_write(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


class Registry:
    def __init__(self, root: Path, config: Config, shared_store=None):
        self.shared_store=shared_store
        self.root = root.resolve()
        self.config = config
        self.skills: dict[str, Skill] = {}
        self.agents: dict[str, Agent] = {}
        self.errors: dict[str, str] = {}
        self.scan()

    def path(self, kind: str, name: str) -> Path:
        if not re.fullmatch(NAME, name):
            raise ValueError("Invalid name; use lowercase letters, digits and underscores")
        path = self.root / kind / (f"{name}.yaml" if kind == "agents" else name)
        # Do not permit symlinked entities to escape the workspace.
        if not path.resolve().is_relative_to((self.root / kind).resolve()):
            raise ValueError("Entity path escapes workspace")
        return path

    def read_skill(self, name: str) -> tuple[Skill, str, str]:
        if name in BUILTINS:
            skill = BUILTINS[name].model_copy(deep=True)
            return skill, yaml.safe_dump(skill.model_dump(), allow_unicode=True), '# Встроенное подключение. Настройте в разделе Подключения.\n'
        path = self.path("skills", name)
        for filename in ("SKILL.md", "skill.py"):
            if (path / filename).is_symlink():
                raise ValueError("Symlinked skill files are not allowed")
        text = (path / "SKILL.md").read_text()
        code = (path / "skill.py").read_text()
        skill = parse_skill(text)
        if skill.name != name:
            raise ValueError("Skill name must match directory")
        tree = ast.parse(code)
        if not any(isinstance(n, ast.FunctionDef) and n.name == "run" for n in tree.body):
            raise ValueError("skill.py must define run(input: dict) -> dict")
        return skill, text, code

    def scan(self):
        skills, agents, errors = {k: v.model_copy(deep=True) for k, v in BUILTINS.items()}, {}, {}
        for path in sorted((self.root / "skills").glob("*/SKILL.md")):
            name = path.parent.name
            try:
                skills[name] = self.read_skill(name)[0]
            except Exception as exc:
                errors[f"skill:{name}"] = str(exc)
        for path in sorted((self.root / "agents").glob("*.yaml")):
            try:
                agent = Agent.model_validate(yaml.safe_load(path.read_text()))
                if agent.name != path.stem or path.is_symlink():
                    raise ValueError("Agent name must match file; symlinks are forbidden")
                agents[agent.name] = agent
            except Exception as exc:
                errors[f"agent:{path.stem}"] = str(exc)
        if self.shared_store:
            for entry in self.shared_store.list():
                agent=Agent.model_validate(entry['agent'])
                agents[agent.name]=agent
        self.skills, self.agents, self.errors = skills, agents, errors

    def build(self, name: str, candidate: Agent | None = None) -> Agent:
        if candidate is None and name.startswith('shared_') and self.shared_store:
            candidate=self.shared_store.get(name)
        if candidate is None and name not in self.agents:
            raise ValueError(self.errors.get(f"agent:{name}", f"Unknown agent: {name}"))
        agent = candidate or self.agents[name]
        memory_path = (self.root / agent.memory.path).resolve()
        if not memory_path.is_relative_to(self.root / "data") or memory_path.suffix != ".db" or memory_path.name == "audit.db":
            raise ValueError("Memory must use a .db file inside data/, distinct from audit.db")
        if memory_path.is_relative_to(self.root / "data/workspace"):
            raise ValueError("Memory cannot be stored in the skill-accessible workspace")
        missing = set(agent.skills) - self.skills.keys()
        if missing:
            raise ValueError(f"Missing or invalid skills: {', '.join(sorted(missing))}")
        if set(agent.confirmation_required) - set(agent.skills):
            raise ValueError("Confirmation actions must be attached skills")
        if set(agent.skill_models) - set(agent.skills):
            raise ValueError("Модель назначена неприкреплённому навыку")
        for ref in agent.skill_models.values():
            if ':' not in ref or ref.split(':', 1)[0] not in self.config.providers:
                raise ValueError("Неизвестная модель навыка")
        provider, _ = agent.model.split(":", 1)
        if provider not in self.config.providers:
            raise ValueError(f"Unknown provider: {provider}")
        if self.config.providers[provider].type not in ("openai", "anthropic", "ollama", "demo"):
            raise ValueError(f"Unsupported provider type: {self.config.providers[provider].type}")
        from superagent.sandbox.policy import resolve_permissions
        for skill_name in agent.skills:
            resolve_permissions(self.root, self.skills[skill_name].permissions)
        # Tools are independent. If two tools use the same field, prohibit conflicting types.
        fields: dict[str, str] = {}
        for skill_name in agent.skills:
            skill = self.skills[skill_name]
            for field, kind in (skill.inputs | skill.outputs).items():
                if field in fields and fields[field] != kind:
                    raise ValueError(f"Incompatible type for shared field '{field}': {fields[field]} / {kind}")
                fields[field] = kind
        return agent

    def save_agent(self, agent: Agent):
        self.build(agent.name, agent)
        atomic_write(self.path("agents", agent.name), yaml.safe_dump(agent.model_dump(), allow_unicode=True, sort_keys=False))
        self.scan()

    def save_skill(self, name: str, metadata: str, code: str):
        if name in BUILTINS:
            raise ValueError("Встроенное подключение нельзя перезаписать")
        skill = parse_skill(metadata)
        if skill.name != name:
            raise ValueError("Skill name must match directory")
        tree = ast.parse(code)
        if not any(isinstance(n, ast.FunctionDef) and n.name == "run" for n in tree.body):
            raise ValueError("Missing run(input) function")
        from superagent.sandbox.policy import resolve_permissions
        resolve_permissions(self.root, skill.permissions)
        path = self.path("skills", name)
        if path.is_symlink() or any((path / f).is_symlink() for f in ("SKILL.md", "skill.py")):
            raise ValueError("Symlinked skills are forbidden")
        atomic_write(path / "skill.py", code)
        atomic_write(path / "SKILL.md", metadata)
        self.scan()
