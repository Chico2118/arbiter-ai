"""Optional AI judge (OFF by default).

- Grades only criteria whose check_type is "judge"; never keyword/regex/human
  criteria, and never decides correctness of programmatically graded items.
- Must answer yes/no with a verbatim quote from the response; a "yes" whose
  quote cannot be found in the response is discarded as unverified.
- Refuses to grade responses from its own model family.
- Its verdicts only count once calibrated against blind human labels for that
  criterion (n >= ARBITER_JUDGE_MIN_N and Cohen's kappa >= ARBITER_JUDGE_MIN_KAPPA).
- Pairwise mode asks twice with positions swapped and flags inconsistent pairs.
"""

from __future__ import annotations

import json
import re
import sqlite3
from typing import Any

from . import config, providers, secrets_store, stats, suites
from .db import dumps, loads, now
from .graders.text import strip_thinking

CRITERION_PROMPT = """You are grading one response against ONE criterion.
Answer strictly in JSON: {{"verdict": "yes" or "no", "quote": "<exact quote from the response that shows the criterion is met, or empty>"}}
The quote must be copied verbatim from the response.

Question asked:
<<<{prompt}>>>

Response to grade:
<<<{response}>>>

Criterion: {criterion}"""

PAIRWISE_PROMPT = """Two responses answer the same question. Which is better overall (accuracy first, then instruction-following, then clarity)?
Answer strictly in JSON: {{"winner": "A" or "B" or "tie"}}

Question:
<<<{prompt}>>>

Response A:
<<<{a}>>>

Response B:
<<<{b}>>>"""


def _json(text: str) -> dict[str, Any] | None:
    m = re.search(r"\{.*\}", strip_thinking(text), re.DOTALL)
    if not m:
        return None
    try:
        value = json.loads(m.group(0))
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower().strip("\"'“”")


def quote_in(quote: str, response: str) -> bool:
    q = _squash(quote)
    return len(q) >= 3 and q in _squash(strip_thinking(response))


def _ask(snap: dict, prompt: str) -> tuple[dict | None, float, str | None]:
    key = None
    if snap["provider"] in providers.NEEDS_KEY or snap["provider"] == "openai_compatible":
        key = secrets_store.get_key(snap["endpoint_id"])
    try:
        out = providers.complete(snap, prompt, key)
    except providers.ProviderError as exc:
        return None, 0.0, str(exc)[:200]
    return _json(out.text), providers.cost_usd(snap, out.input_tokens, out.output_tokens), None


def judge_run(conn: sqlite3.Connection, run_id: int, jsnap: dict, budget: float,
              spent: float, pairwise: bool) -> float:
    run = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    crit = suites.get_criteria(conn, run["suite_version_id"])
    items = {i["id"]: i for i in suites.get_items(conn, run["suite_version_id"])}
    models = {m["id"]: dict(m) | {"snap": loads(m["snapshot"])} for m in conn.execute(
        "SELECT * FROM run_models WHERE run_id = ?", (run_id,))}
    jfam = providers.family(jsnap["model"], jsnap["provider"])
    worst = providers.cost_usd(jsnap, 800, jsnap["max_tokens"])

    responses = conn.execute("SELECT * FROM responses WHERE run_id = ? AND status = 'ok'",
                             (run_id,)).fetchall()
    for resp in responses:
        jcrit = [c for c in crit.get(resp["item_id"], []) if c["check_type"] == "judge"]
        if not jcrit:
            continue
        msnap = models[resp["run_model_id"]]["snap"]
        same_family = providers.family(msnap["model"], msnap["provider"]) == jfam
        for c in jcrit:
            if conn.execute("SELECT 1 FROM criterion_results WHERE response_id = ? AND"
                            " criterion_id = ? AND source = 'judge'",
                            (resp["id"], c["id"])).fetchone():
                continue
            evidence: dict[str, Any] = {"judge_model": jsnap["model"]}
            met = None
            if same_family:
                evidence["skipped"] = f"judge and model share the {jfam} family"
            elif worst > 0 and spent + worst > budget:
                evidence["skipped"] = "budget cap reached"
            else:
                prompt = CRITERION_PROMPT.format(prompt=items[resp["item_id"]]["prompt"],
                                                 response=strip_thinking(resp["text"] or ""),
                                                 criterion=c["text"])
                parsed, cost, err = _ask(jsnap, prompt)
                spent += cost
                verdict = str((parsed or {}).get("verdict", "")).lower().strip()
                quote = str((parsed or {}).get("quote", ""))[:500]
                evidence.update({"verdict": verdict or None, "quote": quote, "error": err})
                if verdict == "no":
                    met = 0
                elif verdict == "yes":
                    verified = quote_in(quote, resp["text"])
                    evidence["quote_verified"] = verified
                    met = 1 if verified else None
            conn.execute("INSERT OR REPLACE INTO criterion_results (response_id, criterion_id,"
                         " source, met, evidence, created_at) VALUES (?,?,?,?,?,?)",
                         (resp["id"], c["id"], "judge", met, dumps(evidence), now()))
        conn.commit()

    if pairwise:
        spent = _pairwise(conn, run_id, jsnap, jfam, models, items, budget, spent, worst)
    return spent


def _pairwise(conn, run_id, jsnap, jfam, models, items, budget, spent, worst) -> float:
    rows = conn.execute("SELECT * FROM comparisons WHERE run_id = ? AND judge_verdict IS NULL",
                        (run_id,)).fetchall()
    for comp in rows:
        texts = {r["id"]: r for r in conn.execute(
            "SELECT id, text, run_model_id FROM responses WHERE id IN (?, ?)",
            (comp["left_response_id"], comp["right_response_id"]))}
        fams = {providers.family(models[t["run_model_id"]]["snap"]["model"],
                                 models[t["run_model_id"]]["snap"]["provider"])
                for t in texts.values()}
        if jfam in fams or (worst > 0 and spent + 2 * worst > budget):
            continue
        left, right = texts[comp["left_response_id"]], texts[comp["right_response_id"]]
        q = items[comp["item_id"]]["prompt"]
        lt, rt = strip_thinking(left["text"] or ""), strip_thinking(right["text"] or "")
        first, c1, _ = _ask(jsnap, PAIRWISE_PROMPT.format(prompt=q, a=lt, b=rt))
        second, c2, _ = _ask(jsnap, PAIRWISE_PROMPT.format(prompt=q, a=rt, b=lt))
        spent += c1 + c2

        def to_side(parsed: dict | None, swapped: bool) -> str | None:
            w = str((parsed or {}).get("winner", "")).strip().upper()
            if w == "TIE":
                return "tie"
            if w not in ("A", "B"):
                return None
            is_left = (w == "A") != swapped
            return "left" if is_left else "right"

        v1, v2 = to_side(first, False), to_side(second, True)
        consistent = None if v1 is None or v2 is None else int(v1 == v2)
        conn.execute("UPDATE comparisons SET judge_verdict = ?, judge_verdict_swapped = ?,"
                     " judge_consistent = ? WHERE id = ?", (v1, v2, consistent, comp["id"]))
        conn.commit()
    return spent


def calibration(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    """Judge-vs-human agreement per criterion key, across all runs."""
    rows = conn.execute(
        "SELECT c.key AS key, h.met AS human, j.met AS judge FROM criterion_results h"
        " JOIN criterion_results j ON j.response_id = h.response_id AND"
        " j.criterion_id = h.criterion_id AND j.source = 'judge'"
        " JOIN hidden_criteria c ON c.id = h.criterion_id"
        " WHERE h.source = 'human' AND h.met IS NOT NULL AND j.met IS NOT NULL").fetchall()
    grouped: dict[str, list[tuple[bool, bool]]] = {}
    for r in rows:
        grouped.setdefault(r["key"], []).append((bool(r["human"]), bool(r["judge"])))
    out = {}
    s = config.settings
    for key, pairs in grouped.items():
        kappa = stats.cohen_kappa([p[0] for p in pairs], [p[1] for p in pairs])
        agree = sum(1 for h, j in pairs if h == j) / len(pairs)
        out[key] = {"n": len(pairs), "kappa": None if kappa is None else round(kappa, 3),
                    "agreement": round(agree, 3),
                    "calibrated": len(pairs) >= s.judge_min_n and kappa is not None
                    and kappa >= s.judge_min_kappa}
    return out


def calibrated_keys(conn: sqlite3.Connection) -> set[str]:
    return {k for k, v in calibration(conn).items() if v["calibrated"]}
