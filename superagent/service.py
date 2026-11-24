from pathlib import Path

from .audit.logger import Audit
from .core.agent_loop import AgentRunner
from .gateway.config import load_config, credential_entries
from .gateway.registry import Registry


class Platform:
    def __init__(self, root: Path, *, config=None):
        self.root = root.resolve()
        (self.root / "data/workspace").mkdir(parents=True, exist_ok=True)
        self.config = config if config is not None else load_config(self.root)
        if (self.root / '.tenant').exists():
            for entry in credential_entries(self.config):
                for field in ('api_key_env','password_env','secret_env'):
                    if hasattr(entry,field): setattr(entry,field,None)
        self.registry = Registry(self.root, self.config)
        self.audit = Audit(self.root, [p.key() for p in credential_entries(self.config)], self.config.audit_max_bytes, self.config.audit_backups)
        from .integrations.hub import Hub
        self.runner = AgentRunner(self.registry, self.audit)

        self.runner.builtins = Hub(self.config, root=self.root, registry=self.registry)

    def close(self):
        for service in getattr(self, "_channel_users", {}).values(): service.close()
        self.audit.close()
