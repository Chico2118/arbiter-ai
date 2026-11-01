"""Suite storage: suites -> versions -> items -> hidden criteria.

A version is locked as soon as a run uses it. Editing a locked version never
mutates it; instead the whole version is copied into a new, unlocked version
and the edit is applied there. Runs therefore always point at the exact items
and criteria they were graded with.
"""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from .db import dumps, loads, now
from .schemas import CriterionIn, ItemIn, SuiteIn

SEED_PATH = Path(__file__).parent / "seed" / "starter_suite.json"


class SuiteError(ValueError):
    pass


def _criterion_key(c: CriterionIn, idx: int) -> str:
    if c.key:
        return c.key
    slug = re.sub(r"[^a-z0-9]+", "_", c.text.lower()).strip("_")[:40]
    return slug or f"c{idx + 1}"


def _criterion_config(c: CriterionIn) -> dict[str, Any]:
    cfg: dict[str, Any] = {}
    if c.check_type == "keywords":
        cfg = {"keywords": c.keywords, "match": c.match}
    elif c.check_type == "regex":
        cfg = {"pattern": c.pattern}
    return cfg


def insert_item(conn: sqlite3.Connection, version_id: int, item: ItemIn, position: int) -> int:
    cur = conn.execute(
        "INSERT INTO items (suite_version_id, key, category, difficulty, prompt, grader,"
        " open_ended, notes, position) VALUES (?,?,?,?,?,?,?,?,?)",
        (version_id, item.key, item.category, item.difficulty, item.prompt,
         dumps(item.grader.model_dump(exclude_defaults=False)), int(item.open_ended),
         item.notes, position),
    )
    item_id = int(cur.lastrowid)
    seen: set[str] = set()
    for idx, c in enumerate(item.criteria):
        key = _criterion_key(c, idx)
        if key in seen:
            key = f"{key}_{idx + 1}"
        seen.add(key)
        conn.execute(
            "INSERT INTO hidden_criteria (item_id, key, text, kind, weight, check_type, config,"
            " failure_tag, position) VALUES (?,?,?,?,?,?,?,?,?)",
            (item_id, key, c.text, c.kind, c.weight, c.check_type,
             dumps(_criterion_config(c)), c.failure_tag, idx),
        )
    return item_id


def create_suite(conn: sqlite3.Connection, suite: SuiteIn, notes: str = "Initial version") -> dict:
    if conn.execute("SELECT 1 FROM suites WHERE name = ?", (suite.name,)).fetchone():
        raise SuiteError(f"A suite named '{suite.name}' already exists")
    sid = int(conn.execute("INSERT INTO suites (name, description, created_at) VALUES (?,?,?)",
                           (suite.name, suite.description, now())).lastrowid)
    vid = int(conn.execute(
        "INSERT INTO suite_versions (suite_id, version, notes, created_at) VALUES (?,?,?,?)",
        (sid, 1, notes, now())).lastrowid)
    for pos, item in enumerate(suite.items):
        insert_item(conn, vid, item, pos)
    return {"suite_id": sid, "version_id": vid}


def load_seed(conn: sqlite3.Connection) -> None:
    """Install the starter suite once, on first start."""
    if conn.execute("SELECT 1 FROM suites LIMIT 1").fetchone():
        return
    data = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    create_suite(conn, SuiteIn.model_validate(data), notes="Starter suite")


def item_row(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["grader"] = loads(d["grader"], {})
    d["open_ended"] = bool(d["open_ended"])
    return d


def criterion_row(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["config"] = loads(d["config"], {})
    return d


def get_items(conn: sqlite3.Connection, version_id: int) -> list[dict]:
    rows = conn.execute("SELECT * FROM items WHERE suite_version_id = ? ORDER BY position, id",
                        (version_id,)).fetchall()
    return [item_row(r) for r in rows]


def get_criteria(conn: sqlite3.Connection, version_id: int) -> dict[int, list[dict]]:
    """Hidden criteria grouped by item id. Only graders and reviewers use this."""
    rows = conn.execute(
        "SELECT c.* FROM hidden_criteria c JOIN items i ON i.id = c.item_id"
        " WHERE i.suite_version_id = ? ORDER BY c.position, c.id", (version_id,)).fetchall()
    out: dict[int, list[dict]] = {}
    for r in rows:
        out.setdefault(r["item_id"], []).append(criterion_row(r))
    return out


def get_version(conn: sqlite3.Connection, version_id: int) -> dict | None:
    row = conn.execute(
        "SELECT v.*, s.name AS suite_name FROM suite_versions v JOIN suites s ON s.id = v.suite_id"
        " WHERE v.id = ?", (version_id,)).fetchone()
    return dict(row) if row else None


def latest_version(conn: sqlite3.Connection, suite_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM suite_versions WHERE suite_id = ? ORDER BY version DESC"
                       " LIMIT 1", (suite_id,)).fetchone()
    return dict(row) if row else None


def lock_version(conn: sqlite3.Connection, version_id: int) -> None:
    conn.execute("UPDATE suite_versions SET locked = 1 WHERE id = ?", (version_id,))


def item_to_in(item: dict, criteria: list[dict]) -> dict:
    """Serialise an item (with its criteria) in the import/export format."""
    crit = []
    for c in criteria:
        entry: dict[str, Any] = {"key": c["key"], "text": c["text"], "kind": c["kind"],
                                 "weight": c["weight"], "check_type": c["check_type"]}
        entry.update(c["config"])
        if c.get("failure_tag"):
            entry["failure_tag"] = c["failure_tag"]
        crit.append(entry)
    grader = {k: v for k, v in item["grader"].items() if v not in (None, [], "")}
    return {"key": item["key"], "category": item["category"], "difficulty": item["difficulty"],
            "prompt": item["prompt"], "grader": grader, "open_ended": item["open_ended"],
            "notes": item["notes"], "criteria": crit}


def export_version(conn: sqlite3.Connection, version_id: int,
                   item_ids: set[int] | None = None) -> dict:
    v = get_version(conn, version_id)
    if v is None:
        raise SuiteError("Suite version not found")
    suite = conn.execute("SELECT * FROM suites WHERE id = ?", (v["suite_id"],)).fetchone()
    crit = get_criteria(conn, version_id)
    items = [item_to_in(i, crit.get(i["id"], [])) for i in get_items(conn, version_id)
             if item_ids is None or i["id"] in item_ids]
    return {"name": suite["name"], "description": suite["description"], "items": items}


def writable_version(conn: sqlite3.Connection, version_id: int, note: str) -> int:
    """Return a version id that may be edited: itself if unlocked, else a new copy."""
    v = get_version(conn, version_id)
    if v is None:
        raise SuiteError("Suite version not found")
    latest = latest_version(conn, v["suite_id"])
    if latest and latest["id"] != version_id and not latest["locked"]:
        raise SuiteError(f"Edit the latest version (v{latest['version']}) instead")
    if not v["locked"]:
        return version_id
    data = export_version(conn, version_id)
    new_no = (latest["version"] if latest else v["version"]) + 1
    new_id = int(conn.execute(
        "INSERT INTO suite_versions (suite_id, version, notes, created_at) VALUES (?,?,?,?)",
        (v["suite_id"], new_no, note[:200], now())).lastrowid)
    for pos, item in enumerate(data["items"]):
        insert_item(conn, new_id, ItemIn.model_validate(item), pos)
    return new_id


def upsert_item(conn: sqlite3.Connection, version_id: int, item: ItemIn) -> dict:
    target = writable_version(conn, version_id, f"Edited item {item.key}")
    existing = conn.execute("SELECT id, position FROM items WHERE suite_version_id = ? AND key = ?",
                            (target, item.key)).fetchone()
    if existing:
        position = existing["position"]
        conn.execute("DELETE FROM items WHERE id = ?", (existing["id"],))
    else:
        row = conn.execute("SELECT COALESCE(MAX(position), -1) + 1 FROM items"
                           " WHERE suite_version_id = ?", (target,)).fetchone()
        position = row[0]
    item_id = insert_item(conn, target, item, position)
    return {"version_id": target, "item_id": item_id, "new_version": target != version_id}


def delete_item(conn: sqlite3.Connection, version_id: int, key: str) -> dict:
    target = writable_version(conn, version_id, f"Deleted item {key}")
    cur = conn.execute("DELETE FROM items WHERE suite_version_id = ? AND key = ?", (target, key))
    if cur.rowcount == 0:
        raise SuiteError("Item not found")
    return {"version_id": target, "new_version": target != version_id}
