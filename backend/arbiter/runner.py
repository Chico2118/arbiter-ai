"""Run creation, cost estimation and background execution.

One worker thread processes queued runs in order, and calls inside a run are
sequential, so a local model only ever handles one request at a time.
"""

from __future__ import annotations

import hashlib
import logging
import queue
import secrets
import sqlite3
import threading
from typing import Any

from . import db, judge, providers, secrets_store, suites
from .db import dumps, loads, now
from .schemas import RunIn

log = logging.getLogger("arbiter.runner")

SNAPSHOT_FIELDS = ("name", "provider", "model", "base_url", "system_prompt", "temperature",
                   "max_tokens", "think", "price_in", "price_out", "timeout_s")
# Settings that change what the model outputs; prices/timeouts/names do not.
CACHE_FIELDS = ("provider", "model", "base_url", "system_prompt", "temperature",
                "max_tokens", "think")
TERMINAL = {"completed", "cancelled", "budget_exceeded", "failed", "interrupted"}

SYNC = False  # tests set this to execute runs inline instead of on the worker
_queue: "queue.Queue[int]" = queue.Queue()
_worker: threading.Thread | None = None


class RunError(ValueError):
    pass


def snapshot(endpoint: dict) -> dict:
    snap = {k: endpoint[k] for k in SNAPSHOT_FIELDS}
    snap["think"] = None if endpoint["think"] is None else bool(endpoint["think"])
    snap["endpoint_id"] = endpoint["id"]
    return snap


def cache_key(snap: dict, prompt: str, repetition: int) -> str:
    """Deterministic key over output-affecting config, the prompt and repetition."""
    payload = dumps({"cfg": {k: snap.get(k) for k in CACHE_FIELDS}, "prompt": prompt,
                     "rep": repetition})
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_prompt(item: dict) -> str:
    """The ONLY item content sent to a model under test: the prompt text.

    Hidden criteria, expected answers, grader settings and notes stay local.
    """
    return item["prompt"]


def _endpoint(conn: sqlite3.Connection, endpoint_id: int) -> dict:
    row = conn.execute("SELECT * FROM endpoints WHERE id = ?", (endpoint_id,)).fetchone()
    if row is None:
        raise RunError(f"Endpoint {endpoint_id} not found")
    return dict(row)


def _selected_items(conn: sqlite3.Connection, spec: RunIn) -> list[dict]:
    items = suites.get_items(conn, spec.suite_version_id)
    if spec.item_ids:
        wanted = set(spec.item_ids)
        items = [i for i in items if i["id"] in wanted]
    if not items:
        raise RunError("The run has no items")
    return items


def estimate(conn: sqlite3.Connection, spec: RunIn) -> dict[str, Any]:
    """Cost preview and data-destination summary shown before a run starts."""
    if suites.get_version(conn, spec.suite_version_id) is None:
        raise RunError("Suite version not found")
    items = _selected_items(conn, spec)
    endpoints = [(_endpoint(conn, spec.candidate_id), "candidate")]
    endpoints += [(_endpoint(conn, b), "baseline") for b in spec.baseline_ids]
    if spec.judge.enabled and spec.judge.endpoint_id is not None:
        endpoints.append((_endpoint(conn, spec.judge.endpoint_id), "judge"))
    rows, total_typ, total_max = [], 0.0, 0.0
    for ep, role in endpoints:
        snap = snapshot(ep)
        calls = len(items) * spec.repetitions
        if role == "judge":
            crit = suites.get_criteria(conn, spec.suite_version_id)
            n_judge = sum(1 for i in items for c in crit.get(i["id"], [])
                          if c["check_type"] == "judge")
            calls = n_judge * spec.repetitions * (1 + len(spec.baseline_ids))
        in_tok = sum(providers.estimate_tokens(snap["system_prompt"] + build_prompt(i))
                     for i in items) * spec.repetitions
        if role == "judge":
            in_tok = calls * 600
        typ = providers.cost_usd(snap, in_tok, calls * min(snap["max_tokens"], 300))
        worst = providers.cost_usd(snap, in_tok, calls * snap["max_tokens"])
        total_typ += typ
        total_max += worst
        dest = providers.destination(snap)
        needs_key = snap["provider"] in providers.NEEDS_KEY
        rows.append({
            "endpoint_id": ep["id"], "name": ep["name"], "role": role, "model": ep["model"],
            "provider": ep["provider"], "company": dest["company"], "host": dest["host"],
            "local": dest["local"], "calls": calls, "est_input_tokens": in_tok,
            "est_cost_typical": round(typ, 4), "est_cost_max": round(worst, 4),
            "key_missing": needs_key and not secrets_store.key_status(ep["id"])["has_key"],
        })
    warnings = []
    if spec.judge.enabled:
        jfam = providers.family(endpoints[-1][0]["model"], endpoints[-1][0]["provider"])
        same = [ep["name"] for ep, role in endpoints[:-1]
                if providers.family(ep["model"], ep["provider"]) == jfam]
        if same:
            warnings.append(f"The judge is in the same model family ({jfam}) as "
                            f"{', '.join(same)}; it will refuse to grade those responses.")
    if total_max > spec.budget_usd:
        warnings.append("The worst-case cost exceeds the budget cap; the run will stop "
                        "once actual spend would cross the cap.")
    return {"items": len(items), "repetitions": spec.repetitions, "endpoints": rows,
            "total_typical": round(total_typ, 4), "total_max": round(total_max, 4),
            "budget_usd": spec.budget_usd, "warnings": warnings}


def create_run(conn: sqlite3.Connection, spec: RunIn) -> int:
    version = suites.get_version(conn, spec.suite_version_id)
    if version is None:
        raise RunError("Suite version not found")
    items = _selected_items(conn, spec)
    models = [(_endpoint(conn, spec.candidate_id), "candidate")]
    models += [(_endpoint(conn, b), "baseline") for b in spec.baseline_ids]
    judge_snap = None
    if spec.judge.enabled and spec.judge.endpoint_id is not None:
        judge_snap = snapshot(_endpoint(conn, spec.judge.endpoint_id))
    suites.lock_version(conn, spec.suite_version_id)  # criteria are frozen from here on
    name = spec.name.strip() or f"{models[0][0]['name']} vs " + ", ".join(
        m[0]["name"] for m in models[1:])
    cfg = {"repetitions": spec.repetitions, "budget_usd": spec.budget_usd,
           "item_ids": [i["id"] for i in items],
           "judge": {**spec.judge.model_dump(), "snapshot": judge_snap}}
    total = len(items) * spec.repetitions * len(models)
    run_id = int(conn.execute(
        "INSERT INTO runs (name, suite_version_id, status, config, created_at, total)"
        " VALUES (?,?,?,?,?,?)",
        (name[:120], spec.suite_version_id, "queued", dumps(cfg), now(), total)).lastrowid)
    for pos, (ep, role) in enumerate(models):
        conn.execute(
            "INSERT INTO run_models (run_id, role, endpoint_id, label, snapshot, position)"
            " VALUES (?,?,?,?,?,?)",
            (run_id, role, ep["id"], ep["name"], dumps(snapshot(ep)), pos))
    return run_id


# --------------------------------------------------------------------------
# Execution
# --------------------------------------------------------------------------

def _status(conn: sqlite3.Connection, run_id: int) -> str:
    return conn.execute("SELECT status FROM runs WHERE id = ?", (run_id,)).fetchone()["status"]


def _call(conn: sqlite3.Connection, snap: dict, prompt: str, rep: int) -> dict[str, Any]:
    key = cache_key(snap, prompt, rep)
    hit = conn.execute("SELECT * FROM response_cache WHERE key = ?", (key,)).fetchone()
    if hit:
        return {"status": "ok", "text": hit["text"], "input_tokens": hit["input_tokens"],
                "output_tokens": hit["output_tokens"], "latency_ms": hit["latency_ms"],
                "cost_usd": 0.0, "cached": 1, "error": None, "truncated": hit["truncated"]}
    api_key = None
    if snap["provider"] in providers.NEEDS_KEY or snap["provider"] == "openai_compatible":
        api_key = secrets_store.get_key(snap["endpoint_id"])
    try:
        out = providers.complete(snap, prompt, api_key)
    except providers.ProviderError as exc:
        return {"status": "error", "text": None, "error": str(exc)[:500], "input_tokens": None,
                "output_tokens": None, "latency_ms": None, "cost_usd": 0.0, "cached": 0,
                "truncated": None}
    conn.execute("INSERT OR REPLACE INTO response_cache (key, text, input_tokens, output_tokens,"
                 " latency_ms, truncated, created_at) VALUES (?,?,?,?,?,?,?)",
                 (key, out.text, out.input_tokens, out.output_tokens, out.latency_ms,
                  int(out.truncated), now()))
    return {"status": "ok", "text": out.text, "input_tokens": out.input_tokens,
            "output_tokens": out.output_tokens, "latency_ms": out.latency_ms,
            "cost_usd": providers.cost_usd(snap, out.input_tokens, out.output_tokens),
            "cached": 0, "error": None, "truncated": int(out.truncated)}


def create_comparisons(conn: sqlite3.Connection, run_id: int) -> int:
    """Blind A/B pairs (candidate vs each baseline) for open-ended items, rep 0.

    The left/right position is randomised with a CSPRNG and stored, so the
    reviewer cannot infer the model from position.
    """
    rng = secrets.SystemRandom()
    models = conn.execute("SELECT * FROM run_models WHERE run_id = ? ORDER BY position",
                          (run_id,)).fetchall()
    cand = next(m for m in models if m["role"] == "candidate")
    run = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    item_ids = set(loads(run["config"])["item_ids"])
    created = 0
    for item in suites.get_items(conn, run["suite_version_id"]):
        if not item["open_ended"] or item["id"] not in item_ids:
            continue
        c_resp = conn.execute("SELECT id FROM responses WHERE run_model_id = ? AND item_id = ?"
                              " AND repetition = 0 AND status = 'ok'",
                              (cand["id"], item["id"])).fetchone()
        if not c_resp:
            continue
        for base in (m for m in models if m["role"] == "baseline"):
            exists = conn.execute("SELECT 1 FROM comparisons WHERE run_id = ? AND item_id = ?"
                                  " AND baseline_model_id = ?",
                                  (run_id, item["id"], base["id"])).fetchone()
            b_resp = conn.execute("SELECT id FROM responses WHERE run_model_id = ? AND item_id = ?"
                                  " AND repetition = 0 AND status = 'ok'",
                                  (base["id"], item["id"])).fetchone()
            if exists or not b_resp:
                continue
            side = rng.choice(["left", "right"])
            left, right = ((c_resp["id"], b_resp["id"]) if side == "left"
                           else (b_resp["id"], c_resp["id"]))
            conn.execute(
                "INSERT INTO comparisons (run_id, item_id, baseline_model_id, left_response_id,"
                " right_response_id, candidate_side, sort_key) VALUES (?,?,?,?,?,?,?)",
                (run_id, item["id"], base["id"], left, right, side, secrets.token_hex(8)))
            created += 1
    return created


def execute_run(run_id: int) -> None:
    conn = db.connect()
    try:
        _execute(conn, run_id)
    except Exception as exc:  # never let the worker die silently
        log.exception("run %s failed", run_id)
        conn.execute("UPDATE runs SET status = 'failed', error = ?, finished_at = ? WHERE id = ?",
                     (f"{type(exc).__name__}: {str(exc)[:300]}", now(), run_id))
        conn.commit()
    finally:
        conn.close()


def _execute(conn: sqlite3.Connection, run_id: int) -> None:
    run = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    if run is None or run["status"] != "queued":
        return
    cfg = loads(run["config"])
    conn.execute("UPDATE runs SET status = 'running', started_at = COALESCE(started_at, ?),"
                 " error = NULL WHERE id = ?", (now(), run_id))
    conn.commit()
    models = [dict(m) | {"snap": loads(m["snapshot"])} for m in conn.execute(
        "SELECT * FROM run_models WHERE run_id = ? ORDER BY position", (run_id,))]
    wanted = set(cfg["item_ids"])
    items = [i for i in suites.get_items(conn, run["suite_version_id"]) if i["id"] in wanted]
    budget = float(cfg["budget_usd"])
    spent = float(run["cost_usd"])
    log.info("run %s started: %d items x %d reps x %d models", run_id, len(items),
             cfg["repetitions"], len(models))

    # Interleave models per item so a partial run still allows paired comparison.
    for item in items:
        prompt = build_prompt(item)
        for rep in range(cfg["repetitions"]):
            for m in models:
                if _status(conn, run_id) == "cancelling":
                    _finish(conn, run_id, "cancelled")
                    return
                done = conn.execute("SELECT status FROM responses WHERE run_model_id = ? AND"
                                    " item_id = ? AND repetition = ?",
                                    (m["id"], item["id"], rep)).fetchone()
                if done and done["status"] == "ok":
                    continue
                snap = m["snap"]
                worst = providers.cost_usd(snap, providers.estimate_tokens(
                    snap["system_prompt"] + prompt), snap["max_tokens"])
                if worst > 0 and spent + worst > budget:
                    _finish(conn, run_id, "budget_exceeded",
                            f"Stopped: the next call could take spend past the "
                            f"${budget:.2f} budget cap (spent ${spent:.4f}).")
                    return
                res = _call(conn, snap, prompt, rep)
                spent += res["cost_usd"]
                conn.execute(
                    "INSERT OR REPLACE INTO responses (run_id, run_model_id, item_id, repetition,"
                    " status, text, error, latency_ms, input_tokens, output_tokens, cost_usd,"
                    " cached, truncated, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (run_id, m["id"], item["id"], rep, res["status"], res["text"], res["error"],
                     res["latency_ms"], res["input_tokens"], res["output_tokens"],
                     res["cost_usd"], res["cached"], res["truncated"], now()))
                conn.execute("UPDATE runs SET done = (SELECT COUNT(*) FROM responses WHERE"
                             " run_id = ?), cost_usd = ? WHERE id = ?", (run_id, spent, run_id))
                conn.commit()

    create_comparisons(conn, run_id)
    conn.commit()
    jcfg = cfg.get("judge") or {}
    if jcfg.get("enabled") and jcfg.get("snapshot"):
        conn.execute("UPDATE runs SET status = 'judging' WHERE id = ?", (run_id,))
        conn.commit()
        spent = judge.judge_run(conn, run_id, jcfg["snapshot"], budget, spent,
                                bool(jcfg.get("pairwise")))
        conn.execute("UPDATE runs SET cost_usd = ? WHERE id = ?", (spent, run_id))
    errors = conn.execute("SELECT COUNT(*) FROM responses WHERE run_id = ? AND status = 'error'",
                          (run_id,)).fetchone()[0]
    _finish(conn, run_id, "completed",
            f"{errors} response(s) failed; see per-item errors." if errors else None)


def _finish(conn: sqlite3.Connection, run_id: int, status: str, error: str | None = None) -> None:
    conn.execute("UPDATE runs SET status = ?, finished_at = ?, error = ? WHERE id = ?",
                 (status, now(), error, run_id))
    conn.commit()
    log.info("run %s finished with status %s", run_id, status)


def _work() -> None:
    while True:
        run_id = _queue.get()
        try:
            execute_run(run_id)
        finally:
            _queue.task_done()


def enqueue(run_id: int) -> None:
    global _worker
    if SYNC:
        execute_run(run_id)
        return
    if _worker is None or not _worker.is_alive():
        _worker = threading.Thread(target=_work, name="arbiter-runner", daemon=True)
        _worker.start()
    _queue.put(run_id)


def recover_interrupted() -> None:
    """Runs left 'running' by a crash/restart become 'interrupted' (resumable)."""
    with db.session() as conn:
        conn.execute("UPDATE runs SET status = 'interrupted', error = 'Server restarted during"
                     " the run; resume to continue.' WHERE status IN"
                     " ('running', 'judging', 'cancelling', 'queued')")
