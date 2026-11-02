"""Where Glidepath keeps your portfolio when it runs as a server.

- DATABASE_URL set (e.g. a free Neon Postgres): stored in Postgres, which
  survives restarts and redeploys. Needs: pip install -r glidepath/requirements-web.txt
- Otherwise: a JSON file at glidepath/data/state.json (fine for running locally;
  on hosts without a permanent disk it is lost on every restart).

The whole app state is one JSON document. Every save carries the version the
device last saw; a save based on an older version is refused so one device
can't silently overwrite another's newer changes.
"""
import json
import os
import threading
import time
from pathlib import Path


class Conflict(Exception):
    def __init__(self, state, updated_at):
        super().__init__("Stored state is newer than this device's copy")
        self.state, self.updated_at = state, updated_at


def _now_ms():
    return int(time.time() * 1000)


class FileStore:
    kind = "file"

    def __init__(self, path):
        self.path = Path(path)
        self._lock = threading.Lock()

    def get(self):
        try:
            doc = json.loads(self.path.read_text())
            return doc["state"], int(doc["updatedAt"])
        except (OSError, ValueError, KeyError):
            return None, 0

    def put(self, state, base):
        with self._lock:
            current, at = self.get()
            if current is not None and base != at:
                raise Conflict(current, at)
            at = max(_now_ms(), at + 1)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"state": state, "updatedAt": at}))
            tmp.replace(self.path)
            return at


class PgStore:
    kind = "postgres"

    def __init__(self, url):
        import psycopg  # imported here so local runs never need it
        self._psycopg = psycopg
        self.url = url
        with self._conn() as c:
            c.execute("""CREATE TABLE IF NOT EXISTS glidepath_state (
                id int PRIMARY KEY, state jsonb NOT NULL, updated_at bigint NOT NULL)""")

    def _conn(self):
        return self._psycopg.connect(self.url, connect_timeout=10, autocommit=False)

    def get(self):
        with self._conn() as c:
            row = c.execute("SELECT state, updated_at FROM glidepath_state WHERE id = 1").fetchone()
        return (row[0], int(row[1])) if row else (None, 0)

    def put(self, state, base):
        from psycopg.types.json import Jsonb
        with self._conn() as c:  # commits on success, rolls back on error
            row = c.execute("SELECT state, updated_at FROM glidepath_state WHERE id = 1 FOR UPDATE").fetchone()
            if row is not None and base != int(row[1]):
                raise Conflict(row[0], int(row[1]))
            at = max(_now_ms(), (int(row[1]) + 1) if row else 0)
            c.execute("""INSERT INTO glidepath_state (id, state, updated_at) VALUES (1, %s, %s)
                         ON CONFLICT (id) DO UPDATE SET state = EXCLUDED.state, updated_at = EXCLUDED.updated_at""",
                      (Jsonb(state), at))
            return at


def make_store(default_dir):
    url = os.environ.get("DATABASE_URL", "").strip()
    if url:
        return PgStore(url)
    return FileStore(Path(default_dir) / "data" / "state.json")
