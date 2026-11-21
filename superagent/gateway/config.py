from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import Field, SecretStr, field_validator, model_validator
from typing import Literal
from urllib.parse import urlsplit

from .models import StrictModel


def endpoint_url(value: str) -> str:
    if value:
        parsed = urlsplit(value)
        if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
            raise ValueError("Use an http(s) URL without embedded credentials or fragment")
    return value.rstrip("/")


class ProviderConfig(StrictModel):
    type: Literal["openai", "anthropic", "ollama", "demo"] = "openai"
    base_url: str = "https://api.openai.com/v1"
    api_key_env: str | None = "OPENAI_API_KEY"
    api_key: SecretStr | None = None
    models: list[str] = Field(default_factory=list)

    _url = field_validator("base_url")(endpoint_url)

    def key(self) -> str:
        return (os.environ.get(self.api_key_env, "") if self.api_key_env else "") or (self.api_key.get_secret_value() if self.api_key else "")


class SearchConfig(StrictModel):
    enabled: bool = False
    engine: Literal["tavily", "brave", "searxng"] = "tavily"
    base_url: str = "https://api.tavily.com"
    api_key_env: str | None = "TAVILY_API_KEY"
    api_key: SecretStr | None = None
    _url = field_validator("base_url")(endpoint_url)

    def key(self):
        return (os.environ.get(self.api_key_env, "") if self.api_key_env else "") or (self.api_key.get_secret_value() if self.api_key else "")


class PasswordConnection(StrictModel):
    enabled: bool = False
    username: str = ""
    password_env: str | None = None
    password: SecretStr | None = None

    def key(self):
        return (os.environ.get(self.password_env, "") if self.password_env else "") or (self.password.get_secret_value() if self.password else "")


class IMAPConfig(PasswordConnection):
    host: str = ""
    port: int = Field(default=993, ge=1, le=65535)
    security: Literal["tls", "starttls"] = "tls"
    folders: list[str] = Field(default_factory=lambda: ["INBOX"])
    password_env: str | None = "IMAP_PASSWORD"

    @model_validator(mode="after")
    def valid_connection(self):
        if any(c in self.host + self.username + "".join(self.folders) for c in "\r\n\x00"):
            raise ValueError("Control characters are forbidden")
        if self.enabled and (not self.host or not self.username or not self.folders):
            raise ValueError("IMAP needs host, username and at least one folder")
        return self


class CalDAVConfig(PasswordConnection):
    url: str = ""
    password_env: str | None = "CALDAV_PASSWORD"
    calendars: list[str] = Field(default_factory=list)
    _url = field_validator("url")(endpoint_url)

    @model_validator(mode="after")
    def valid_connection(self):
        if self.enabled and (not self.url or not self.username):
            raise ValueError("CalDAV needs a server URL and username")
        return self


class RSSFeed(StrictModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    url: str = Field(min_length=1)
    _url = field_validator("url")(endpoint_url)


class RSSConfig(StrictModel):
    enabled: bool = False
    feeds: list[RSSFeed] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def unique_feeds(self):
        if len({f.name for f in self.feeds}) != len(self.feeds):
            raise ValueError("RSS feed names must be unique")
        return self


class SSHConfig(PasswordConnection):
    host: str = ''
    port: int = Field(default=22,ge=1,le=65535)
    host_key_sha256: str = ''
    commands: list[str] = Field(default_factory=lambda:['uptime','df -h','free -m'])


class OneCConfig(PasswordConnection):
    url: str = ''
    entities: list[str] = Field(default_factory=list)
    _url = field_validator('url')(endpoint_url)


class WorkConnection(PasswordConnection):
    url: str = ''
    _url = field_validator('url')(endpoint_url)

    @model_validator(mode='after')
    def configured_url(self):
        if self.enabled and not self.url: raise ValueError('Укажите адрес сервиса')
        from urllib.parse import urlsplit
        if self.url and urlsplit(self.url).query: raise ValueError('Адрес сервиса должен быть без query-параметров')
        return self


class AtlassianConfig(WorkConnection):
    edition: Literal['cloud','server'] = 'cloud'
    auth_mode: Literal['basic','bearer'] = 'basic'


class RedmineConfig(WorkConnection):
    auth_mode: Literal['basic','api_key'] = 'basic'


class BitrixConfig(WorkConnection):
    crm_enabled: bool = False

    @model_validator(mode='after')
    def webhook_config(self):
        if '/rest/' in self.url or self.url.endswith('/rest'):
            raise ValueError('Укажите только адрес портала; ID пользователя — в username, токен webhook — в password')
        if self.enabled and not self.username.isdigit(): raise ValueError('ID пользователя webhook должен быть числом')
        return self


class Connections(StrictModel):
    jira: AtlassianConfig = Field(default_factory=AtlassianConfig)
    confluence: AtlassianConfig = Field(default_factory=AtlassianConfig)
    redmine: RedmineConfig = Field(default_factory=RedmineConfig)
    bitrix24: BitrixConfig = Field(default_factory=BitrixConfig)
    one_c: OneCConfig = Field(default_factory=OneCConfig)
    ssh: SSHConfig = Field(default_factory=SSHConfig)
    search: SearchConfig = Field(default_factory=SearchConfig)
    imap: IMAPConfig = Field(default_factory=IMAPConfig)
    caldav: CalDAVConfig = Field(default_factory=CalDAVConfig)
    rss: RSSConfig = Field(default_factory=RSSConfig)


class ChannelConfig(StrictModel):
    type: Literal['telegram', 'mattermost', 'xmpp']
    agent: str = 'office_assistant'
    enabled: bool = False
    url: str = ''
    username: str = ''
    secret_env: str | None = None
    secret: SecretStr | None = None
    allowed: list[str] = Field(default_factory=list)

    def key(self):
        return (os.environ.get(self.secret_env, '') if self.secret_env else '') or (self.secret.get_secret_value() if self.secret else '')


class Config(StrictModel):
    channels: dict[str, ChannelConfig] = Field(default_factory=dict)
    connections: Connections = Field(default_factory=Connections)
    providers: dict[str, ProviderConfig] = Field(default_factory=lambda: {
        "openai": ProviderConfig(models=["gpt-4o-mini"]),
        "deepseek": ProviderConfig(base_url="https://api.deepseek.com", api_key_env="DEEPSEEK_API_KEY", models=["deepseek-flash", "deepseek-v4-pro"]),
        "anthropic": ProviderConfig(type="anthropic", base_url="https://api.anthropic.com", api_key_env="ANTHROPIC_API_KEY", models=["claude-sonnet-4-5"]),
        "ollama": ProviderConfig(type="ollama", base_url="http://localhost:11434", api_key_env=None, models=["qwen3:8b"]),
        "demo": ProviderConfig(type="demo", api_key_env=None, models=["office"]),
    })
    skill_timeout: float = Field(default=60, gt=0, le=600)
    model_timeout: float = Field(default=120, gt=0, le=600)
    confirmation_timeout: float = Field(default=300, gt=0, le=3600)
    context_turns: int = Field(default=20, ge=1, le=500)
    audit_max_bytes: int = Field(default=5_000_000, ge=1024)
    audit_backups: int = Field(default=3, ge=1, le=100)


def load_config(root: Path) -> Config:
    path = root / "config.yaml"
    return Config.model_validate(yaml.safe_load(path.read_text()) or {}) if path.exists() else Config()



def credential_entries(config):
    return [*config.providers.values(), *[value for _,value in config.connections if hasattr(value,'key')], *config.channels.values()]
