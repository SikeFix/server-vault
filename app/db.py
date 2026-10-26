import os
import sqlite3
import threading
from contextlib import contextmanager

from .config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS servers(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE,
  host TEXT NOT NULL,
  port INTEGER NOT NULL DEFAULT 22,
  username TEXT NOT NULL DEFAULT 'root',
  auth_type TEXT NOT NULL DEFAULT 'password',
  password_enc TEXT,
  private_key_enc TEXT,
  key_passphrase_enc TEXT,
  pending_password_enc TEXT,
  use_sudo INTEGER NOT NULL DEFAULT 0,
  group_name TEXT NOT NULL DEFAULT '默认',
  tags TEXT NOT NULL DEFAULT '',
  location TEXT NOT NULL DEFAULT '',
  provider TEXT NOT NULL DEFAULT '',
  notes TEXT NOT NULL DEFAULT '',
  host_key TEXT,
  host_key_fp TEXT,
  rotation_enabled INTEGER NOT NULL DEFAULT 0,
  rotation_days INTEGER NOT NULL DEFAULT 30,
  last_rotated_at TEXT,
  next_rotation_at TEXT,
  rotation_status TEXT,
  rotation_error TEXT,
  status TEXT NOT NULL DEFAULT 'unknown',
  status_detail TEXT,
  latency_ms INTEGER,
  last_checked_at TEXT,
  facts_json TEXT,
  facts_at TEXT,
  sites_json TEXT,
  sites_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS password_history(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  server_id INTEGER NOT NULL REFERENCES servers(id) ON DELETE CASCADE,
  password_enc TEXT NOT NULL,
  reason TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_tokens(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  token_hash TEXT NOT NULL UNIQUE,
  prefix TEXT NOT NULL,
  scopes TEXT NOT NULL,
  group_scope TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  expires_at TEXT,
  last_used_at TEXT,
  revoked INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS automations(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  script TEXT NOT NULL,
  target_type TEXT NOT NULL DEFAULT 'all',
  target_value TEXT NOT NULL DEFAULT '',
  interval_minutes INTEGER NOT NULL DEFAULT 0,
  use_sudo INTEGER NOT NULL DEFAULT 0,
  enabled INTEGER NOT NULL DEFAULT 1,
  last_run_at TEXT,
  last_status TEXT,
  last_result TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_logs(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  actor TEXT NOT NULL,
  action TEXT NOT NULL,
  server_id INTEGER,
  server_name TEXT,
  detail TEXT,
  ok INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_logs(ts);
CREATE INDEX IF NOT EXISTS idx_audit_server ON audit_logs(server_id);
"""


class DB:
    def __init__(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        new = not path.exists()
        self.conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)
        self._migrate()
        if new:
            os.chmod(path, 0o600)

    def _migrate(self):
        cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(servers)")}
        for col, ddl in (("proxy_command", "TEXT NOT NULL DEFAULT ''"), ("source", "TEXT NOT NULL DEFAULT ''")):
            if col not in cols:
                self.conn.execute(f"ALTER TABLE servers ADD COLUMN {col} {ddl}")

    def all(self, sql, params=()):
        with self.lock:
            return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    def one(self, sql, params=()):
        with self.lock:
            r = self.conn.execute(sql, params).fetchone()
            return dict(r) if r else None

    def run(self, sql, params=()):
        with self.lock:
            return self.conn.execute(sql, params).lastrowid

    @contextmanager
    def tx(self):
        with self.lock:
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                yield
            except BaseException:
                self.conn.execute("ROLLBACK")
                raise
            self.conn.execute("COMMIT")

    def get_meta(self, key, default=None):
        r = self.one("SELECT value FROM meta WHERE key=?", (key,))
        return r["value"] if r else default

    def set_meta(self, key, value):
        self.run("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))


db = DB(DB_PATH)
