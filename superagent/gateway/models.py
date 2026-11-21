from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

NAME = r"^[a-z][a-z0-9_]{0,63}$"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Permissions(StrictModel):
    filesystem: list[str] = Field(default_factory=list)
    network: list[str] = Field(default_factory=list)
    subprocess: bool = False

    @field_validator("network")
    @classmethod
    def valid_hosts(cls, values):
        for value in values:
            host, sep, port = value.rpartition(":")
            if not sep or not host or not port.isdigit() or not 1 <= int(port) <= 65535:
                raise ValueError("Network permissions must be host:port")
        return values


def type_schema(value: str) -> dict:
    value = value.strip()
    primitives = {"str": "string", "int": "integer", "float": "number", "bool": "boolean", "dict": "object", "list": "array", "any": None}
    if value in primitives:
        return {"type": primitives[value]} if primitives[value] else {}
    match = re.fullmatch(r"list\[(.+)\]", value)
    if match:
        return {"type": "array", "items": type_schema(match[1])}
    raise ValueError(f"Unsupported field type: {value}")


class Skill(StrictModel):
    name: str = Field(pattern=NAME)
    description: str = Field(min_length=1, max_length=4000)
    version: str = "0.1.0"
    inputs: dict[str, str] = Field(default_factory=dict)
    outputs: dict[str, str] = Field(default_factory=dict)
    permissions: Permissions = Field(default_factory=Permissions)
    irreversible: bool = False

    @field_validator("inputs", "outputs")
    @classmethod
    def valid_types(cls, values):
        for key, value in values.items():
            if not re.fullmatch(NAME, key):
                raise ValueError(f"Invalid field name: {key}")
            type_schema(value)
        return values

    def tool(self) -> dict:
        return {"type": "function", "function": {"name": self.name, "description": self.description,
            "parameters": {"type": "object", "properties": {k: type_schema(v) for k, v in self.inputs.items()},
                           "required": list(self.inputs), "additionalProperties": False}}}


class MemoryConfig(StrictModel):
    type: Literal["sqlite"] = "sqlite"
    path: str = "./data/memory.db"


class Agent(StrictModel):
    name: str = Field(pattern=NAME)
    model: str = Field(pattern=r"^[a-z][a-z0-9_-]*:.+$")
    instructions: str = Field(min_length=1)
    skills: list[str] = Field(default_factory=list)
    skill_models: dict[str, str] = Field(default_factory=dict)
    confirmation_required: list[str] = Field(default_factory=list)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    max_steps: int = Field(default=10, ge=1, le=100)

    @field_validator("skills", "confirmation_required")
    @classmethod
    def unique_names(cls, values):
        if len(values) != len(set(values)) or any(not re.fullmatch(NAME, v) for v in values):
            raise ValueError("Expected unique valid skill names")
        return values


def validate_payload(payload: dict, fields: dict[str, str], *, exact: bool = True):
    if not isinstance(payload, dict):
        raise ValueError("Expected a JSON object")
    if exact and set(payload) != set(fields):
        raise ValueError(f"Expected fields: {', '.join(fields) or '(none)'}")

    def check(value, schema):
        kind = schema.get("type")
        valid = {"string": lambda: isinstance(value, str), "integer": lambda: type(value) is int,
                 "number": lambda: type(value) in (int, float), "boolean": lambda: type(value) is bool,
                 "object": lambda: isinstance(value, dict), "array": lambda: isinstance(value, list)}
        if kind and not valid[kind]():
            raise ValueError(f"Expected {kind}")
        if kind == "array" and "items" in schema:
            for item in value:
                check(item, schema["items"])

    for key, value_type in fields.items():
        if key not in payload:
            raise ValueError(f"Missing field: {key}")
        try:
            check(payload[key], type_schema(value_type))
        except ValueError as exc:
            raise ValueError(f"{key}: {exc}") from exc
