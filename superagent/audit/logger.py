from __future__ import annotations

import json
import re
import sqlite3
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from uuid import uuid4

import logging


class Audit:
    def __init__(self, root: Path, secrets: list[str], max_bytes=5_000_000, backups=3, *, user: str | None = None):
        self.root, self.secrets = root, [s for s in secrets if s]
        self._user = ContextVar("audit_user", default=user)
        (root / "data").mkdir(parents=True, exist_ok=True)
        self.db = root / "data/audit.db"
        self.lock = threading.RLock()
        self.logger = logging.Logger(f"audit.{uuid4().hex}", level=logging.INFO)
        handler = RotatingFileHandler(root / "data/audit.log", maxBytes=max_bytes, backupCount=backups, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(message)s"))
        self.logger.addHandler(handler)
        with sqlite3.connect(self.db) as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, time TEXT, agent TEXT, skill TEXT, status TEXT, kind TEXT, payload TEXT)")

    def redact(self, value):
        if isinstance(value, dict):
            return {k: "[REDACTED]" if re.search(r"api.?key|authorization|password|secret|token", k, re.I) else self.redact(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self.redact(v) for v in value]
        if isinstance(value, str):
            for secret in self.secrets:
                value = value.replace(secret, "[REDACTED]")
        return value

    @contextmanager
    def as_user(self, user: str | None):
        token = self._user.set(user)
        try:
            yield
        finally:
            self._user.reset(token)

    def record(self, *, agent: str, kind: str, skill: str | None = None, input=None, output=None, status="ok", duration_ms=0, session_id=None):
        entry = self.redact({"time": datetime.now(timezone.utc).isoformat(), "user": self._user.get(), "agent": agent, "kind": kind,
                             "skill": skill, "input": input, "output": output, "status": status,
                             "duration_ms": round(duration_ms, 2), "session_id": session_id})
        text = json.dumps(entry, ensure_ascii=False)
        with self.lock:
            self.logger.info(text)
            with sqlite3.connect(self.db) as db:
                db.execute("INSERT INTO events(time, agent, skill, status, kind, payload) VALUES(?,?,?,?,?,?)",
                           (entry["time"], agent, skill, status, kind, text))
        return entry

    def query(self, agent=None, skill=None, status=None, date=None, limit=100, offset=0):
        clauses, params = [], []
        for column, value in (("agent", agent), ("skill", skill), ("status", status)):
            if value:
                clauses.append(f"{column} = ?")
                params.append(value)
        if date:
            clauses.append("time LIKE ?")
            params.append(date + "%")
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with sqlite3.connect(self.db) as db:
            rows = db.execute("SELECT payload FROM events" + where + " ORDER BY id DESC LIMIT ? OFFSET ?", [*params, min(limit, 500), offset]).fetchall()
        return [json.loads(row[0]) for row in rows]

    def close(self):
        for handler in self.logger.handlers[:]:
            handler.close()
            self.logger.removeHandler(handler)
