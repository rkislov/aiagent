import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


class Memory:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, agent TEXT NOT NULL, title TEXT NOT NULL, created TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY, session_id TEXT NOT NULL, payload TEXT NOT NULL)")

    def connect(self):
        return sqlite3.connect(self.path, timeout=15)

    def ensure(self, session_id: str, agent: str, title: str):
        with self.connect() as db:
            row = db.execute("SELECT agent FROM sessions WHERE id=?", (session_id,)).fetchone()
            if row and row[0] != agent:
                raise ValueError("Session belongs to another agent")
            db.execute("INSERT OR IGNORE INTO sessions VALUES(?,?,?,?)", (session_id, agent, title[:100], datetime.now(timezone.utc).isoformat()))

    def web(self, session_id, enabled=None):
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS dialog_settings(id TEXT PRIMARY KEY, web INTEGER NOT NULL DEFAULT 0)')
            if enabled is not None:
                db.execute('INSERT INTO dialog_settings VALUES(?,?) ON CONFLICT(id) DO UPDATE SET web=excluded.web', (session_id, int(enabled)))
            row = db.execute('SELECT web FROM dialog_settings WHERE id=?', (session_id,)).fetchone()
            return bool(row[0]) if row else False

    def load(self, session_id: str) -> list[dict]:
        with self.connect() as db:
            return [json.loads(row[0]) for row in db.execute("SELECT payload FROM messages WHERE session_id=? ORDER BY id", (session_id,))]

    def knowledge(self,session_id,collections=None):
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS dialog_knowledge(id TEXT PRIMARY KEY,collections TEXT NOT NULL)')
            if collections is not None:
                db.execute('INSERT INTO dialog_knowledge VALUES(?,?) ON CONFLICT(id) DO UPDATE SET collections=excluded.collections',(session_id,json.dumps(list(collections))))
            row=db.execute('SELECT collections FROM dialog_knowledge WHERE id=?',(session_id,)).fetchone()
            return json.loads(row[0]) if row else []

    def append(self, session_id: str, message: dict):
        with self.connect() as db:
            db.execute("INSERT INTO messages(session_id, payload) VALUES(?,?)", (session_id, json.dumps(message, ensure_ascii=False)))

    def sessions(self, agent: str):
        with self.connect() as db:
            return [dict(zip(("id", "agent", "title", "created"), row)) for row in db.execute("SELECT * FROM sessions WHERE agent=? ORDER BY created DESC LIMIT 100", (agent,))]
