"""HTTP API. Every route validates input with the Pydantic models in schemas.py."""

from __future__ import annotations

import json
import re
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Query, Response

from . import __version__, config, db, judge, providers, report, review, runner, secrets_store, suites
from .db import loads, now
from .schemas import (
    ApiKeyIn, CriteriaReviewIn, EndpointIn, GateIn, ItemIn, RunIn, SuiteImport, SuiteIn,
    TagsIn, VoteIn,
)

router = APIRouter(prefix="/api")

ENDPOINT_COLS = ("name", "provider", "model", "base_url", "system_prompt", "temperature",
                 "max_tokens", "think", "price_in", "price_out", "timeout_s")


def _404(what: str) -> HTTPException:
    return HTTPException(404, f"{what} not found")


def _download(content: str, filename: str, media: str) -> Response:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", filename).strip("-")[:100] or "export"
    return Response(content, media_type=media,
                    headers={"Content-Disposition": f'attachment; filename="{safe}"'})


# --------------------------------------------------------------------------
# Health / overview
# --------------------------------------------------------------------------

@router.get("/health")
def health() -> dict[str, Any]:
    ollama: dict[str, Any] = {"url": config.settings.ollama_url, "reachable": False, "models": []}
    try:
        with providers.client(3.0) as c:
            r = c.get(f"{config.settings.ollama_url}/api/tags")
            if r.status_code == 200:
                ollama["reachable"] = True
                ollama["models"] = [m.get("name") for m in r.json().get("models", [])][:50]
    except (httpx.HTTPError, ValueError):
        pass
    return {"ok": True, "version": __version__, "ollama": ollama}


@router.get("/overview")
def overview() -> dict[str, Any]:
    with db.session() as conn:
        runs = [dict(r) for r in conn.execute(
            "SELECT id, name, status, done, total, cost_usd, created_at FROM runs"
            " ORDER BY id DESC LIMIT 8")]
        counts = {
            "runs": conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0],
            "suites": conn.execute("SELECT COUNT(*) FROM suites").fetchone()[0],
            "endpoints": conn.execute("SELECT COUNT(*) FROM endpoints").fetchone()[0],
        }
        eps = [_endpoint_out(dict(r)) for r in conn.execute("SELECT * FROM endpoints ORDER BY name")]
        suite_rows = _suite_list(conn)
        votes, crit = review.vote_counts(conn), review.criteria_counts(conn)
    return {"counts": counts, "recent_runs": runs, "endpoints": eps, "suites": suite_rows,
            "review": {"votes_pending": votes["total"] - votes["done"],
                       "criteria_pending": crit["total"] - crit["done"]}}


# --------------------------------------------------------------------------
# Endpoints (model connections)
# --------------------------------------------------------------------------

def _endpoint_out(row: dict) -> dict[str, Any]:
    row = dict(row)
    row["think"] = None if row["think"] is None else bool(row["think"])
    snap = runner.snapshot(row)
    row["destination"] = providers.destination(snap)
    row["needs_key"] = row["provider"] in providers.NEEDS_KEY
    row["key"] = secrets_store.key_status(row["id"])
    return row


def _endpoint_values(ep: EndpointIn) -> tuple:
    d = ep.model_dump()
    d["think"] = None if d["think"] is None else int(d["think"])
    return tuple(d[c] for c in ENDPOINT_COLS)


@router.get("/endpoints")
def list_endpoints() -> list[dict]:
    with db.session() as conn:
        return [_endpoint_out(dict(r)) for r in conn.execute("SELECT * FROM endpoints ORDER BY name")]


@router.post("/endpoints", status_code=201)
def create_endpoint(ep: EndpointIn) -> dict:
    with db.session() as conn:
        if conn.execute("SELECT 1 FROM endpoints WHERE name = ?", (ep.name,)).fetchone():
            raise HTTPException(409, "An endpoint with this name already exists")
        cols = ", ".join(ENDPOINT_COLS)
        marks = ", ".join("?" * len(ENDPOINT_COLS))
        eid = conn.execute(f"INSERT INTO endpoints ({cols}, created_at, updated_at)"
                           f" VALUES ({marks}, ?, ?)", (*_endpoint_values(ep), now(), now())).lastrowid
        return _endpoint_out(dict(conn.execute("SELECT * FROM endpoints WHERE id = ?",
                                               (eid,)).fetchone()))


@router.put("/endpoints/{endpoint_id}")
def update_endpoint(endpoint_id: int, ep: EndpointIn) -> dict:
    with db.session() as conn:
        if not conn.execute("SELECT 1 FROM endpoints WHERE id = ?", (endpoint_id,)).fetchone():
            raise _404("Endpoint")
        clash = conn.execute("SELECT 1 FROM endpoints WHERE name = ? AND id != ?",
                             (ep.name, endpoint_id)).fetchone()
        if clash:
            raise HTTPException(409, "An endpoint with this name already exists")
        sets = ", ".join(f"{c} = ?" for c in ENDPOINT_COLS)
        conn.execute(f"UPDATE endpoints SET {sets}, updated_at = ? WHERE id = ?",
                     (*_endpoint_values(ep), now(), endpoint_id))
        return _endpoint_out(dict(conn.execute("SELECT * FROM endpoints WHERE id = ?",
                                               (endpoint_id,)).fetchone()))


@router.delete("/endpoints/{endpoint_id}", status_code=204)
def delete_endpoint(endpoint_id: int) -> Response:
    with db.session() as conn:
        cur = conn.execute("DELETE FROM endpoints WHERE id = ?", (endpoint_id,))
        if cur.rowcount == 0:
            raise _404("Endpoint")
    try:
        secrets_store.delete_key(endpoint_id)
    except Exception:
        pass
    return Response(status_code=204)


@router.put("/endpoints/{endpoint_id}/key")
def set_endpoint_key(endpoint_id: int, body: ApiKeyIn) -> dict:
    with db.session() as conn:
        if not conn.execute("SELECT 1 FROM endpoints WHERE id = ?", (endpoint_id,)).fetchone():
            raise _404("Endpoint")
    try:
        secrets_store.set_key(endpoint_id, body.api_key)
    except Exception:
        raise HTTPException(500, "Could not write to the OS credential store") from None
    return secrets_store.key_status(endpoint_id)  # masked only, never the key


@router.delete("/endpoints/{endpoint_id}/key")
def delete_endpoint_key(endpoint_id: int) -> dict:
    secrets_store.delete_key(endpoint_id)
    return secrets_store.key_status(endpoint_id)


@router.post("/endpoints/{endpoint_id}/test")
def test_endpoint(endpoint_id: int) -> dict:
    with db.session() as conn:
        row = conn.execute("SELECT * FROM endpoints WHERE id = ?", (endpoint_id,)).fetchone()
    if row is None:
        raise _404("Endpoint")
    snap = runner.snapshot(dict(row))
    key = None
    if snap["provider"] in providers.NEEDS_KEY or snap["provider"] == "openai_compatible":
        try:
            key = secrets_store.get_key(endpoint_id)
        except Exception:
            return {"ok": False, "message": "Could not read the OS credential store"}
    return providers.test_connection(snap, key)


# --------------------------------------------------------------------------
# Suites
# --------------------------------------------------------------------------

def _suite_list(conn) -> list[dict]:
    out = []
    for s in conn.execute("SELECT * FROM suites ORDER BY id"):
        versions = [dict(v) | {"items": conn.execute(
            "SELECT COUNT(*) FROM items WHERE suite_version_id = ?", (v["id"],)).fetchone()[0]}
            for v in conn.execute("SELECT * FROM suite_versions WHERE suite_id = ?"
                                  " ORDER BY version DESC", (s["id"],))]
        out.append(dict(s) | {"versions": versions, "latest_version_id": versions[0]["id"]
                              if versions else None})
    return out


@router.get("/suites")
def list_suites() -> list[dict]:
    with db.session() as conn:
        return _suite_list(conn)


@router.post("/suites", status_code=201)
def create_suite(body: SuiteIn) -> dict:
    with db.session() as conn:
        try:
            return suites.create_suite(conn, body)
        except suites.SuiteError as exc:
            raise HTTPException(409, str(exc)) from None


@router.post("/suites/import", status_code=201)
def import_suite(body: SuiteImport) -> dict:
    with db.session() as conn:
        try:
            return suites.create_suite(conn, body.suite, notes=f"Imported from {body.filename}")
        except suites.SuiteError as exc:
            raise HTTPException(409, str(exc)) from None


@router.get("/suite-versions/{version_id}")
def get_suite_version(version_id: int) -> dict:
    with db.session() as conn:
        v = suites.get_version(conn, version_id)
        if v is None:
            raise _404("Suite version")
        crit = suites.get_criteria(conn, version_id)
        items = [i | {"criteria": crit.get(i["id"], [])} for i in suites.get_items(conn, version_id)]
        runs = conn.execute("SELECT COUNT(*) FROM runs WHERE suite_version_id = ?",
                            (version_id,)).fetchone()[0]
    return {"version": v, "items": items, "runs": runs}


@router.get("/suite-versions/{version_id}/export")
def export_suite_version(version_id: int) -> Response:
    with db.session() as conn:
        try:
            data = suites.export_version(conn, version_id)
        except suites.SuiteError:
            raise _404("Suite version") from None
        v = suites.get_version(conn, version_id)
    return _download(json.dumps(data, indent=2, ensure_ascii=False),
                     f"{v['suite_name']}-v{v['version']}.json", "application/json")


@router.put("/suite-versions/{version_id}/items")
def upsert_item(version_id: int, item: ItemIn) -> dict:
    with db.session() as conn:
        try:
            return suites.upsert_item(conn, version_id, item)
        except suites.SuiteError as exc:
            raise HTTPException(409, str(exc)) from None


@router.delete("/suite-versions/{version_id}/items/{key}")
def delete_item(version_id: int, key: str) -> dict:
    with db.session() as conn:
        try:
            return suites.delete_item(conn, version_id, key)
        except suites.SuiteError as exc:
            raise HTTPException(409, str(exc)) from None


# --------------------------------------------------------------------------
# Runs
# --------------------------------------------------------------------------

@router.post("/runs/estimate")
def estimate_run(spec: RunIn) -> dict:
    with db.session() as conn:
        try:
            return runner.estimate(conn, spec)
        except runner.RunError as exc:
            raise HTTPException(400, str(exc)) from None


@router.post("/runs", status_code=201)
def create_run(spec: RunIn) -> dict:
    with db.session() as conn:
        try:
            est = runner.estimate(conn, spec)
            missing = [e["name"] for e in est["endpoints"] if e["key_missing"]]
            if missing:
                raise HTTPException(400, f"Missing API key for: {', '.join(missing)}")
            run_id = runner.create_run(conn, spec)
        except runner.RunError as exc:
            raise HTTPException(400, str(exc)) from None
    runner.enqueue(run_id)
    return {"id": run_id}


@router.get("/runs")
def list_runs() -> list[dict]:
    with db.session() as conn:
        rows = conn.execute(
            "SELECT r.id, r.name, r.status, r.done, r.total, r.cost_usd, r.created_at,"
            " r.finished_at, r.error, v.version AS suite_version, s.name AS suite"
            " FROM runs r JOIN suite_versions v ON v.id = r.suite_version_id"
            " JOIN suites s ON s.id = v.suite_id ORDER BY r.id DESC").fetchall()
        out = []
        for r in rows:
            models = [dict(m) for m in conn.execute(
                "SELECT id, role, label FROM run_models WHERE run_id = ? ORDER BY position", (r["id"],))]
            out.append(dict(r) | {"models": models})
        return out


@router.get("/runs/{run_id}")
def get_run(run_id: int) -> dict:
    with db.session() as conn:
        row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            raise _404("Run")
        run = dict(row)
        run["config"] = loads(run["config"], {})
        if run["config"].get("judge", {}).get("snapshot"):
            run["config"]["judge"]["snapshot"].pop("system_prompt", None)
        models = []
        for m in conn.execute("SELECT * FROM run_models WHERE run_id = ? ORDER BY position", (run_id,)):
            snap = loads(m["snapshot"])
            models.append({"id": m["id"], "role": m["role"], "label": m["label"],
                           "endpoint_id": m["endpoint_id"], "snapshot": snap,
                           "destination": providers.destination(snap)})
        v = suites.get_version(conn, run["suite_version_id"])
        errors = conn.execute("SELECT COUNT(*) FROM responses WHERE run_id = ? AND status = 'error'",
                              (run_id,)).fetchone()[0]
    return run | {"models": models, "suite": v["suite_name"], "suite_version": v["version"],
                  "errors": errors}


@router.get("/runs/{run_id}/items")
def run_items(run_id: int) -> list[dict]:
    with db.session() as conn:
        data = report.load_run(conn, run_id)
    if data is None:
        raise _404("Run")
    by_item: dict[int, dict[str, Any]] = {}
    for iid, item in data.items.items():
        by_item[iid] = {"id": iid, "key": item["key"], "category": item["category"],
                        "difficulty": item["difficulty"], "prompt": item["prompt"],
                        "grader": item["grader"], "open_ended": item["open_ended"],
                        "responses": []}
    for r in data.responses:
        if r["item_id"] not in by_item:
            continue
        v = data.verdicts[r["id"]]
        by_item[r["item_id"]]["responses"].append({
            "id": r["id"], "run_model_id": r["run_model_id"], "repetition": r["repetition"],
            "status": r["status"], "text": r["text"], "error": r["error"],
            "latency_ms": r["latency_ms"], "input_tokens": r["input_tokens"],
            "output_tokens": r["output_tokens"], "cost_usd": r["cost_usd"],
            "cached": bool(r["cached"]), "truncated": bool(r.get("truncated")),
            "verdict": v.to_dict(),
            "tags": sorted(data.tags.get(r["id"], set())),
        })
    return list(by_item.values())


@router.post("/runs/{run_id}/cancel")
def cancel_run(run_id: int) -> dict:
    with db.session() as conn:
        row = conn.execute("SELECT status FROM runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            raise _404("Run")
        if row["status"] in ("queued", "running", "judging"):
            new = "cancelling" if row["status"] != "queued" else "cancelled"
            conn.execute("UPDATE runs SET status = ? WHERE id = ?", (new, run_id))
            return {"status": new}
    return {"status": row["status"]}


@router.post("/runs/{run_id}/resume")
def resume_run(run_id: int) -> dict:
    with db.session() as conn:
        row = conn.execute("SELECT status FROM runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            raise _404("Run")
        if row["status"] not in ("interrupted", "cancelled", "budget_exceeded", "failed",
                                 "completed"):
            raise HTTPException(409, f"Run is {row['status']}")
        conn.execute("UPDATE runs SET status = 'queued', finished_at = NULL WHERE id = ?", (run_id,))
    runner.enqueue(run_id)
    return {"status": "queued"}


@router.delete("/runs/{run_id}", status_code=204)
def delete_run(run_id: int) -> Response:
    with db.session() as conn:
        row = conn.execute("SELECT status FROM runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            raise _404("Run")
        if row["status"] in ("running", "judging", "cancelling"):
            raise HTTPException(409, "Cancel the run before deleting it")
        conn.execute("DELETE FROM runs WHERE id = ?", (run_id,))
    return Response(status_code=204)


@router.put("/responses/{response_id}/tags")
def set_tags(response_id: int, body: TagsIn) -> dict:
    with db.session() as conn:
        if not conn.execute("SELECT 1 FROM responses WHERE id = ?", (response_id,)).fetchone():
            raise _404("Response")
        conn.execute("DELETE FROM response_tags WHERE response_id = ?", (response_id,))
        for t in sorted(set(body.tags)):
            conn.execute("INSERT INTO response_tags (response_id, tag) VALUES (?, ?)",
                         (response_id, t))
    return {"tags": sorted(set(body.tags))}


# --------------------------------------------------------------------------
# Blind review
# --------------------------------------------------------------------------

@router.get("/review/counts")
def review_counts(run_id: int | None = Query(default=None)) -> dict:
    with db.session() as conn:
        return {"votes": review.vote_counts(conn, run_id),
                "criteria": review.criteria_counts(conn, run_id)}


@router.get("/review/votes/next")
def next_vote(run_id: int | None = Query(default=None)) -> dict:
    with db.session() as conn:
        task = review.next_vote(conn, run_id)
        return {"task": task, "progress": review.vote_counts(conn, run_id)}


@router.post("/review/votes/{comparison_id}")
def submit_vote(comparison_id: int, body: VoteIn) -> dict:
    with db.session() as conn:
        if not review.record_vote(conn, comparison_id, body.vote, list(body.reason_tags), body.note):
            raise _404("Comparison")
    return {"ok": True}


@router.get("/review/criteria/next")
def next_criteria(run_id: int | None = Query(default=None)) -> dict:
    with db.session() as conn:
        task = review.next_criteria(conn, run_id)
        return {"task": task, "progress": review.criteria_counts(conn, run_id)}


@router.post("/review/criteria/{response_id}")
def submit_criteria(response_id: int, body: CriteriaReviewIn) -> dict:
    with db.session() as conn:
        try:
            review.record_criteria(conn, response_id, [(l.criterion_id, l.met) for l in body.labels])
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
    return {"ok": True}


# --------------------------------------------------------------------------
# Reports
# --------------------------------------------------------------------------

def _report(run_id: int) -> dict:
    with db.session() as conn:
        rep = report.build_report(conn, run_id)
    if rep is None:
        raise _404("Run")
    return rep


@router.get("/runs/{run_id}/report")
def get_report(run_id: int) -> dict:
    return _report(run_id)


@router.get("/runs/{run_id}/report.md")
def report_markdown(run_id: int) -> Response:
    return _download(report.to_markdown(_report(run_id)), f"arbiter-run-{run_id}.md",
                     "text/markdown; charset=utf-8")


@router.get("/runs/{run_id}/report.json")
def report_json(run_id: int) -> Response:
    return _download(json.dumps(_report(run_id), indent=2, ensure_ascii=False, default=str),
                     f"arbiter-run-{run_id}.json", "application/json")


@router.get("/runs/{run_id}/failed-items")
def failed_items(run_id: int) -> Response:
    with db.session() as conn:
        data = report.failed_items_suite(conn, run_id)
    if data is None:
        raise _404("Run")
    return _download(json.dumps(data, indent=2, ensure_ascii=False),
                     f"arbiter-run-{run_id}-failures.json", "application/json")


@router.post("/runs/{run_id}/gate")
def release_gate(run_id: int, body: GateIn) -> dict:
    rep = _report(run_id)
    try:
        return report.gate(rep, **body.model_dump())
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None


@router.get("/history")
def get_history() -> list[dict]:
    with db.session() as conn:
        return report.history(conn)


@router.get("/judge/calibration")
def get_calibration() -> dict:
    with db.session() as conn:
        return {"criteria": judge.calibration(conn), "min_n": config.settings.judge_min_n,
                "min_kappa": config.settings.judge_min_kappa}
