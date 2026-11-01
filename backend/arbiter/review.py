"""Blind human review: A/B preference votes and criterion checklists.

Nothing returned from here identifies the model, endpoint, role or run model
behind a response. Positions in A/B pairs were randomised at creation time.
"""

from __future__ import annotations

import hashlib
import sqlite3
from typing import Any

from . import answers
from .db import dumps, loads, now


def _run_filter(run_id: int | None) -> tuple[str, tuple]:
    return ("AND c.run_id = ?", (run_id,)) if run_id else ("", ())


def vote_counts(conn: sqlite3.Connection, run_id: int | None = None) -> dict[str, int]:
    where, args = _run_filter(run_id)
    row = conn.execute(f"SELECT COUNT(*) AS total, SUM(vote IS NOT NULL) AS done FROM comparisons c"
                       f" WHERE 1=1 {where}", args).fetchone()
    return {"total": row["total"] or 0, "done": row["done"] or 0}


def next_vote(conn: sqlite3.Connection, run_id: int | None = None) -> dict[str, Any] | None:
    where, args = _run_filter(run_id)
    comp = conn.execute(f"SELECT c.* FROM comparisons c WHERE c.vote IS NULL {where}"
                        f" ORDER BY c.sort_key LIMIT 1", args).fetchone()
    if comp is None:
        return None
    item = conn.execute("SELECT prompt, category FROM items WHERE id = ?",
                        (comp["item_id"],)).fetchone()
    inline = answers.inline_reasoning_models(conn, comp["run_id"])
    left = answers.load_response(conn, comp["left_response_id"])
    right = answers.load_response(conn, comp["right_response_id"])
    # Final answers only: inline reasoning can reveal the system prompt.
    return {"comparison_id": comp["id"], "prompt": item["prompt"], "category": item["category"],
            "a_text": answers.display_text(left, inline),
            "b_text": answers.display_text(right, inline),
            "progress": vote_counts(conn, run_id)}


def record_vote(conn: sqlite3.Connection, comparison_id: int, vote: str, tags: list[str],
                note: str) -> bool:
    cur = conn.execute("UPDATE comparisons SET vote = ?, reason_tags = ?, note = ?, voted_at = ?"
                       " WHERE id = ?", (vote, dumps(sorted(set(tags))), note, now(),
                                         comparison_id))
    return cur.rowcount == 1


def _human_tasks(conn: sqlite3.Connection, run_id: int | None) -> list[dict[str, Any]]:
    """Responses that have human- or judge-type criteria still lacking a human label."""
    sql = ("SELECT r.id AS response_id, r.item_id, r.run_id, c.id AS criterion_id FROM responses r"
           " JOIN hidden_criteria c ON c.item_id = r.item_id"
           " WHERE r.status = 'ok' AND c.check_type IN ('human', 'judge')")
    args: tuple = ()
    if run_id:
        sql += " AND r.run_id = ?"
        args = (run_id,)
    labelled = {(r["response_id"], r["criterion_id"]) for r in conn.execute(
        "SELECT response_id, criterion_id FROM criterion_results WHERE source = 'human'")}
    tasks: dict[int, dict[str, Any]] = {}
    inline_by_run: dict[int, set[int]] = {}
    for r in conn.execute(sql, args):
        if r["run_id"] not in inline_by_run:
            inline_by_run[r["run_id"]] = answers.inline_reasoning_models(conn, r["run_id"])
        if r["response_id"] not in tasks:
            resp = answers.load_response(conn, r["response_id"])
            if answers.has_no_final_answer(resp, inline_by_run[r["run_id"]]):
                continue  # nothing to judge: graded as incorrect automatically
        t = tasks.setdefault(r["response_id"], {"response_id": r["response_id"],
                                                "item_id": r["item_id"], "missing": 0,
                                                "total": 0})
        t["total"] += 1
        if (r["response_id"], r["criterion_id"]) not in labelled:
            t["missing"] += 1
    return list(tasks.values())


def criteria_counts(conn: sqlite3.Connection, run_id: int | None = None) -> dict[str, int]:
    tasks = _human_tasks(conn, run_id)
    return {"total": len(tasks), "done": sum(1 for t in tasks if t["missing"] == 0)}


def next_criteria(conn: sqlite3.Connection, run_id: int | None = None) -> dict[str, Any] | None:
    pending = [t for t in _human_tasks(conn, run_id) if t["missing"] > 0]
    if not pending:
        return None
    # Hash ordering interleaves models so consecutive tasks don't reveal a model.
    pending.sort(key=lambda t: hashlib.sha256(f"arbiter:{t['response_id']}".encode()).hexdigest())
    task = pending[0]
    resp = answers.load_response(conn, task["response_id"])
    item = conn.execute("SELECT prompt, category FROM items WHERE id = ?",
                        (task["item_id"],)).fetchone()
    crit = conn.execute("SELECT id, text, kind FROM hidden_criteria WHERE item_id = ? AND"
                        " check_type IN ('human', 'judge') ORDER BY position, id",
                        (task["item_id"],)).fetchall()
    return {"response_id": task["response_id"], "prompt": item["prompt"],
            "category": item["category"],
            "response_text": answers.display_text(resp, answers.inline_reasoning_models(conn, resp["run_id"])),
            "criteria": [{"id": c["id"], "text": c["text"], "kind": c["kind"]} for c in crit],
            "progress": criteria_counts(conn, run_id)}


def record_criteria(conn: sqlite3.Connection, response_id: int,
                    labels: list[tuple[int, bool]]) -> None:
    resp = conn.execute("SELECT item_id FROM responses WHERE id = ?", (response_id,)).fetchone()
    if resp is None:
        raise ValueError("Response not found")
    valid = {r["id"] for r in conn.execute(
        "SELECT id FROM hidden_criteria WHERE item_id = ? AND check_type IN ('human', 'judge')",
        (resp["item_id"],))}
    for cid, met in labels:
        if cid not in valid:
            raise ValueError(f"Criterion {cid} does not belong to this response's item")
    for cid, met in labels:
        conn.execute("INSERT OR REPLACE INTO criterion_results (response_id, criterion_id, source,"
                     " met, evidence, created_at) VALUES (?,?,?,?,?,?)",
                     (response_id, cid, "human", int(met), "{}", now()))


def labels_for(conn: sqlite3.Connection, response_ids: list[int]
               ) -> dict[int, dict[tuple[int, str], dict[str, Any]]]:
    """criterion_results grouped per response for the grading code."""
    out: dict[int, dict[tuple[int, str], dict[str, Any]]] = {}
    if not response_ids:
        return out
    for chunk_start in range(0, len(response_ids), 500):
        chunk = response_ids[chunk_start:chunk_start + 500]
        marks = ",".join("?" * len(chunk))
        for r in conn.execute(f"SELECT * FROM criterion_results WHERE response_id IN ({marks})",
                              chunk):
            out.setdefault(r["response_id"], {})[(r["criterion_id"], r["source"])] = {
                "met": r["met"], "evidence": loads(r["evidence"], {})}
    return out

