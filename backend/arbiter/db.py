"""SQLite storage (stdlib sqlite3).

Every caller opens its own short-lived connection via ``connect()``; the
background runner thread does the same. WAL mode keeps readers and the single
writer from blocking each other.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS endpoints (
    id            INTEGER PRIMARY KEY,
    name          TEXT NOT NULL UNIQUE,
    provider      TEXT NOT NULL,
    model         TEXT NOT NULL,
    base_url      TEXT NOT NULL DEFAULT '',
    system_prompt TEXT NOT NULL DEFAULT '',
    temperature   REAL NOT NULL DEFAULT 0,
    max_tokens    INTEGER NOT NULL DEFAULT 512,
    think         INTEGER,
    price_in      REAL NOT NULL DEFAULT 0,
    price_out     REAL NOT NULL DEFAULT 0,
    timeout_s     REAL NOT NULL DEFAULT 300,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS suites (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS suite_versions (
    id         INTEGER PRIMARY KEY,
    suite_id   INTEGER NOT NULL REFERENCES suites(id) ON DELETE CASCADE,
    version    INTEGER NOT NULL,
    locked     INTEGER NOT NULL DEFAULT 0,
    notes      TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    UNIQUE (suite_id, version)
);

CREATE TABLE IF NOT EXISTS items (
    id               INTEGER PRIMARY KEY,
    suite_version_id INTEGER NOT NULL REFERENCES suite_versions(id) ON DELETE CASCADE,
    key              TEXT NOT NULL,
    category         TEXT NOT NULL,
    difficulty       TEXT NOT NULL DEFAULT 'medium',
    prompt           TEXT NOT NULL,
    grader           TEXT NOT NULL,
    open_ended       INTEGER NOT NULL DEFAULT 0,
    notes            TEXT NOT NULL DEFAULT '',
    position         INTEGER NOT NULL DEFAULT 0,
    UNIQUE (suite_version_id, key)
);

-- Hidden rubric criteria live in their own table and are never joined into
-- anything that builds a model request (see runner.build_messages).
CREATE TABLE IF NOT EXISTS hidden_criteria (
    id          INTEGER PRIMARY KEY,
    item_id     INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    key         TEXT NOT NULL,
    text        TEXT NOT NULL,
    kind        TEXT NOT NULL,
    weight      REAL NOT NULL DEFAULT 1,
    check_type  TEXT NOT NULL,
    config      TEXT NOT NULL DEFAULT '{}',
    failure_tag TEXT,
    position    INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS runs (
    id               INTEGER PRIMARY KEY,
    name             TEXT NOT NULL,
    suite_version_id INTEGER NOT NULL REFERENCES suite_versions(id),
    status           TEXT NOT NULL,
    config           TEXT NOT NULL,
    created_at       TEXT NOT NULL,
    started_at       TEXT,
    finished_at      TEXT,
    error            TEXT,
    done             INTEGER NOT NULL DEFAULT 0,
    total            INTEGER NOT NULL DEFAULT 0,
    cost_usd         REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS run_models (
    id          INTEGER PRIMARY KEY,
    run_id      INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    role        TEXT NOT NULL,
    endpoint_id INTEGER,
    label       TEXT NOT NULL,
    snapshot    TEXT NOT NULL,
    position    INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS responses (
    id            INTEGER PRIMARY KEY,
    run_id        INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    run_model_id  INTEGER NOT NULL REFERENCES run_models(id) ON DELETE CASCADE,
    item_id       INTEGER NOT NULL REFERENCES items(id),
    repetition    INTEGER NOT NULL,
    status        TEXT NOT NULL,
    text          TEXT,
    error         TEXT,
    latency_ms    REAL,
    input_tokens  INTEGER,
    output_tokens INTEGER,
    cost_usd      REAL NOT NULL DEFAULT 0,
    cached        INTEGER NOT NULL DEFAULT 0,
    truncated     INTEGER,
    created_at    TEXT NOT NULL,
    UNIQUE (run_model_id, item_id, repetition)
);

CREATE TABLE IF NOT EXISTS criterion_results (
    id           INTEGER PRIMARY KEY,
    response_id  INTEGER NOT NULL REFERENCES responses(id) ON DELETE CASCADE,
    criterion_id INTEGER NOT NULL REFERENCES hidden_criteria(id),
    source       TEXT NOT NULL,
    met          INTEGER,
    evidence     TEXT NOT NULL DEFAULT '{}',
    created_at   TEXT NOT NULL,
    UNIQUE (response_id, criterion_id, source)
);

CREATE TABLE IF NOT EXISTS comparisons (
    id                    INTEGER PRIMARY KEY,
    run_id                INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    item_id               INTEGER NOT NULL REFERENCES items(id),
    baseline_model_id     INTEGER NOT NULL REFERENCES run_models(id),
    left_response_id      INTEGER NOT NULL REFERENCES responses(id),
    right_response_id     INTEGER NOT NULL REFERENCES responses(id),
    candidate_side        TEXT NOT NULL,
    sort_key              TEXT NOT NULL,
    vote                  TEXT,
    reason_tags           TEXT NOT NULL DEFAULT '[]',
    note                  TEXT NOT NULL DEFAULT '',
    voted_at              TEXT,
    judge_verdict         TEXT,
    judge_verdict_swapped TEXT,
    judge_consistent      INTEGER
);

CREATE TABLE IF NOT EXISTS response_tags (
    id          INTEGER PRIMARY KEY,
    response_id INTEGER NOT NULL REFERENCES responses(id) ON DELETE CASCADE,
    tag         TEXT NOT NULL,
    UNIQUE (response_id, tag)
);

CREATE TABLE IF NOT EXISTS response_cache (
    key           TEXT PRIMARY KEY,
    text          TEXT NOT NULL,
    input_tokens  INTEGER,
    output_tokens INTEGER,
    latency_ms    REAL,
    truncated     INTEGER,
    created_at    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_responses_run ON responses(run_id);
CREATE INDEX IF NOT EXISTS idx_items_version ON items(suite_version_id);
CREATE INDEX IF NOT EXISTS idx_criteria_item ON hidden_criteria(item_id);
CREATE INDEX IF NOT EXISTS idx_cr_response ON criterion_results(response_id);
CREATE INDEX IF NOT EXISTS idx_comparisons_run ON comparisons(run_id);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    path = config.settings.db_path
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


@contextmanager
def session() -> Iterator[sqlite3.Connection]:
    """Connection that commits on success and always closes."""
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# Columns added after the first release: (table, column, type).
MIGRATIONS = [("responses", "truncated", "INTEGER"), ("response_cache", "truncated", "INTEGER")]


def init_db() -> None:
    with session() as conn:
        conn.executescript(SCHEMA)
        for table, column, ctype in MIGRATIONS:
            cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
            if column not in cols:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ctype}")


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def loads(value: str | None, default: Any = None) -> Any:
    if value is None or value == "":
        return default
    return json.loads(value)


def row_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None
