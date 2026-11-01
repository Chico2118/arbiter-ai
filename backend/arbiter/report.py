"""Gap report: every number is computed from stored responses and labels and
carries the item ids that back it ("evidence"), so the UI can link to them.

Unit of analysis is the ITEM. With repetitions, an item counts as correct for
a model when at least half of its graded repetitions are correct; repetitions
are not treated as independent samples (that would overstate confidence).
"""

from __future__ import annotations

import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any

from . import answers, config, graders, judge, review, stats, suites
from .db import loads
from .providers import destination

NEGATIVE_REASON_TAGS = {"hallucinated": "hallucination", "unsafe": "unsafe"}


@dataclass
class RunData:
    run: dict
    version: dict
    models: list[dict]
    items: dict[int, dict]
    criteria: dict[int, list[dict]]
    responses: list[dict]
    verdicts: dict[int, graders.Verdict]
    tags: dict[int, set[str]] = field(default_factory=dict)
    comparisons: list[dict] = field(default_factory=list)
    calibrated: set[str] = field(default_factory=set)

    @property
    def candidate(self) -> dict:
        return next(m for m in self.models if m["role"] == "candidate")

    @property
    def baselines(self) -> list[dict]:
        return [m for m in self.models if m["role"] == "baseline"]


def load_run(conn: sqlite3.Connection, run_id: int) -> RunData | None:
    run = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    if run is None:
        return None
    run = dict(run)
    run["config"] = loads(run["config"], {})
    version = suites.get_version(conn, run["suite_version_id"]) or {}
    models = []
    for m in conn.execute("SELECT * FROM run_models WHERE run_id = ? ORDER BY position", (run_id,)):
        d = dict(m)
        d["snapshot"] = loads(d["snapshot"], {})
        d["destination"] = destination(d["snapshot"])
        models.append(d)
    wanted = set(run["config"].get("item_ids", []))
    items = {i["id"]: i for i in suites.get_items(conn, run["suite_version_id"])
             if not wanted or i["id"] in wanted}
    criteria = suites.get_criteria(conn, run["suite_version_id"])
    responses = [dict(r) for r in conn.execute(
        "SELECT * FROM responses WHERE run_id = ? ORDER BY item_id, repetition", (run_id,))]
    max_tokens = {m["id"]: m["snapshot"].get("max_tokens") for m in models}
    for r in responses:
        # Responses stored before truncation tracking existed: infer it from
        # the output reaching the configured max-token limit.
        if r.get("truncated") is None and r["status"] == "ok" and r["output_tokens"]:
            limit = max_tokens.get(r["run_model_id"])
            r["truncated"] = int(bool(limit) and r["output_tokens"] >= limit)
    inline = answers.inline_reasoning_models(conn, run_id)
    for r in responses:
        r["no_final_answer"] = answers.has_no_final_answer(r, inline)
    labels = review.labels_for(conn, [r["id"] for r in responses])
    calibrated = judge.calibrated_keys(conn)
    verdicts = {r["id"]: graders.evaluate(items[r["item_id"]], criteria.get(r["item_id"], []), r,
                                          labels.get(r["id"], {}), calibrated)
                for r in responses if r["item_id"] in items}
    tags: dict[int, set[str]] = defaultdict(set)
    for r in conn.execute("SELECT t.response_id, t.tag FROM response_tags t JOIN responses r"
                          " ON r.id = t.response_id WHERE r.run_id = ?", (run_id,)):
        tags[r["response_id"]].add(r["tag"])
    comparisons = []
    for c in conn.execute("SELECT * FROM comparisons WHERE run_id = ?", (run_id,)):
        d = dict(c)
        d["reason_tags"] = loads(d["reason_tags"], [])
        comparisons.append(d)
    return RunData(run, version, models, items, criteria, responses, verdicts, tags,
                   comparisons, calibrated)


# --------------------------------------------------------------------------
# Item-level outcomes
# --------------------------------------------------------------------------

def item_outcomes(data: RunData) -> dict[int, dict[int, dict[str, Any]]]:
    """{run_model_id: {item_id: {correct, reps, consistent, statuses}}}."""
    grouped: dict[int, dict[int, list]] = defaultdict(lambda: defaultdict(list))
    for r in data.responses:
        if r["item_id"] in data.items:
            grouped[r["run_model_id"]][r["item_id"]].append(data.verdicts[r["id"]])
    out: dict[int, dict[int, dict[str, Any]]] = {}
    for mid, per_item in grouped.items():
        out[mid] = {}
        for iid, vs in per_item.items():
            decided = [v.correct for v in vs if v.correct is not None]
            correct = None if not decided else sum(decided) * 2 >= len(decided)
            out[mid][iid] = {
                "correct": correct,
                "reps": len(vs),
                "decided": len(decided),
                "consistent": None if len(decided) < 2 else len(set(decided)) == 1,
                "statuses": [v.status for v in vs],
            }
    return out


def _acc(outcomes: dict[int, dict[str, Any]], item_ids: list[int]) -> dict[str, Any]:
    decided = [i for i in item_ids if outcomes.get(i, {}).get("correct") is not None]
    right = [i for i in decided if outcomes[i]["correct"]]
    wrong = [i for i in decided if not outcomes[i]["correct"]]
    n = len(decided)
    lo, hi = stats.wilson(len(right), n)
    return {"k": len(right), "n": n, "acc": (len(right) / n) if n else None,
            "ci_low": lo if n else None, "ci_high": hi if n else None,
            "evidence": {"correct": right, "incorrect": wrong}}


def _by(data: RunData, attr: str) -> dict[str, list[int]]:
    groups: dict[str, list[int]] = defaultdict(list)
    for iid, item in data.items.items():
        groups[item[attr]].append(iid)
    return dict(sorted(groups.items()))


def _paired(cand: dict, base: dict, item_ids: list[int], min_n: int) -> dict[str, Any]:
    cells: dict[str, list[int]] = {"both_right": [], "only_candidate": [], "only_baseline": [],
                                   "both_wrong": []}
    for i in item_ids:
        a, b = cand.get(i, {}).get("correct"), base.get(i, {}).get("correct")
        if a is None or b is None:
            continue
        key = ("both_right" if a and b else "only_candidate" if a else
               "only_baseline" if b else "both_wrong")
        cells[key].append(i)
    n = sum(len(v) for v in cells.values())
    oc, ob = len(cells["only_candidate"]), len(cells["only_baseline"])
    p = stats.mcnemar_exact(oc, ob)
    cand_acc = (len(cells["both_right"]) + oc) / n if n else None
    base_acc = (len(cells["both_right"]) + ob) / n if n else None
    if n < min_n:
        verdict, label = "insufficient", f"not enough paired items (n={n} < {min_n})"
    elif p < 0.05:
        verdict = "better" if oc > ob else "worse"
        label = f"significant: candidate {verdict} (p={p:.3f})"
    else:
        verdict, label = "no_difference", f"no significant difference (p={p:.3f})"
    return {"n": n, "counts": {k: len(v) for k, v in cells.items()}, "evidence": cells,
            "p_value": p, "verdict": verdict, "label": label,
            "candidate_acc": cand_acc, "baseline_acc": base_acc,
            "gap_points": None if n == 0 else round((cand_acc - base_acc) * 100, 1)}


def _response_tags(data: RunData) -> dict[int, Counter]:
    """Failure tags per response: automatic + human + (vote-attributed)."""
    out: dict[int, Counter] = defaultdict(Counter)
    for r in data.responses:
        v = data.verdicts.get(r["id"])
        for t in (v.auto_tags if v else []):
            out[r["id"]][t] += 1
        for t in data.tags.get(r["id"], set()):
            out[r["id"]][t] += 1
    for c in data.comparisons:
        if not c["vote"]:
            continue
        losers = {"left": [c["right_response_id"]], "right": [c["left_response_id"]],
                  "tie": [], "both_bad": [c["left_response_id"], c["right_response_id"]]}
        for tag in c["reason_tags"]:
            if tag in NEGATIVE_REASON_TAGS:
                for rid in losers[c["vote"]]:
                    out[rid][NEGATIVE_REASON_TAGS[tag]] += 1
    return out


def _model_ref(m: dict) -> dict[str, Any]:
    s = m["snapshot"]
    return {"id": m["id"], "label": m["label"], "role": m["role"], "provider": s["provider"],
            "model": s["model"], "local": m["destination"]["local"],
            "company": m["destination"]["company"], "system_prompt": s.get("system_prompt", ""),
            "temperature": s.get("temperature"), "think": s.get("think"),
            "max_tokens": s.get("max_tokens")}


def build_report(conn: sqlite3.Connection, run_id: int) -> dict[str, Any] | None:
    data = load_run(conn, run_id)
    if data is None:
        return None
    min_n = config.settings.min_paired_n
    outcomes = item_outcomes(data)
    cand = data.candidate
    all_ids = list(data.items)
    by_cat, by_diff = _by(data, "category"), _by(data, "difficulty")
    resp_by_model: dict[int, list[dict]] = defaultdict(list)
    for r in data.responses:
        resp_by_model[r["run_model_id"]].append(r)
    rtags = _response_tags(data)

    accuracy = {}
    for m in data.models:
        o = outcomes.get(m["id"], {})
        accuracy[m["id"]] = {
            "overall": _acc(o, all_ids),
            "by_category": {c: _acc(o, ids) for c, ids in by_cat.items()},
            "by_difficulty": {d: _acc(o, ids) for d, ids in by_diff.items()},
        }

    paired, flips = {}, {}
    for b in data.baselines:
        co, bo = outcomes.get(cand["id"], {}), outcomes.get(b["id"], {})
        paired[b["id"]] = {"overall": _paired(co, bo, all_ids, min_n),
                           "by_category": {c: _paired(co, bo, ids, min_n)
                                           for c, ids in by_cat.items()}}
        ov = paired[b["id"]]["overall"]["evidence"]
        flips[b["id"]] = {"regressions": ov["only_baseline"], "improvements": ov["only_candidate"]}

    votes = _votes(data)
    rubric = _rubric(data, resp_by_model)
    compliance = _compliance(data, resp_by_model)
    consistency = {}
    for m in data.models:
        multi = {i: o for i, o in outcomes.get(m["id"], {}).items() if o["consistent"] is not None}
        stable = [i for i, o in multi.items() if o["consistent"]]
        consistency[m["id"]] = {"n": len(multi), "consistent": len(stable),
                                "rate": len(stable) / len(multi) if multi else None,
                                "evidence": {"inconsistent": [i for i, o in multi.items()
                                                              if not o["consistent"]]}}
    failures = {}
    for m in data.models:
        counts: Counter = Counter()
        evidence: dict[str, set[int]] = defaultdict(set)
        for r in resp_by_model[m["id"]]:
            for tag in rtags.get(r["id"], {}):
                counts[tag] += 1
                evidence[tag].add(r["item_id"])
        failures[m["id"]] = {"counts": dict(counts.most_common()),
                             "evidence": {k: sorted(v) for k, v in evidence.items()}}
    efficiency = {}
    for m in data.models:
        rs = resp_by_model[m["id"]]
        ok = [r for r in rs if r["status"] == "ok"]
        live = [r for r in ok if not r["cached"]]
        cost = sum(r["cost_usd"] or 0 for r in rs)
        k = accuracy[m["id"]]["overall"]["k"]
        efficiency[m["id"]] = {
            "responses": len(rs), "errors": len(rs) - len(ok), "cached": len(ok) - len(live),
            "latency_mean_ms": stats.mean(r["latency_ms"] for r in live),
            "latency_median_ms": stats.med(r["latency_ms"] for r in live),
            "input_tokens": sum(r["input_tokens"] or 0 for r in ok),
            "output_tokens": sum(r["output_tokens"] or 0 for r in ok),
            "output_tokens_mean": stats.mean(r["output_tokens"] for r in ok),
            "cost_usd": round(cost, 6), "correct_items": k,
            "cost_per_correct": round(cost / k, 6) if k else None,
            "truncated": sum(1 for r in ok if r.get("truncated")),
            "evidence": {"errors": sorted({r["item_id"] for r in rs if r["status"] != "ok"}),
                         "truncated": sorted({r["item_id"] for r in ok if r.get("truncated")})},
        }

    calib = judge.calibration(conn)
    judge_keys = sorted({c["key"] for iid in data.items for c in data.criteria.get(iid, [])
                         if c["check_type"] == "judge"})
    judge_section = {
        "enabled": bool(data.run["config"].get("judge", {}).get("enabled")),
        "min_n": config.settings.judge_min_n, "min_kappa": config.settings.judge_min_kappa,
        "criteria": [{"key": k, **calib.get(k, {"n": 0, "kappa": None, "agreement": None,
                                                "calibrated": False})} for k in judge_keys],
        "pairwise": _judge_pairwise(data),
    }

    items_index = {iid: {"key": i["key"], "category": i["category"],
                         "difficulty": i["difficulty"], "prompt": i["prompt"][:160]}
                   for iid, i in data.items.items()}
    report = {
        "run": {"id": data.run["id"], "name": data.run["name"], "status": data.run["status"],
                "created_at": data.run["created_at"], "finished_at": data.run["finished_at"],
                "suite": data.version.get("suite_name"), "suite_version": data.version.get("version"),
                "repetitions": data.run["config"].get("repetitions", 1),
                "cost_usd": round(data.run["cost_usd"], 6), "items": len(data.items)},
        "models": [_model_ref(m) for m in data.models],
        "candidate_id": cand["id"],
        "categories": list(by_cat), "difficulties": list(by_diff),
        "min_paired_n": min_n,
        "accuracy": accuracy, "paired": paired, "flips": flips, "votes": votes,
        "rubric": rubric, "compliance": compliance, "consistency": consistency,
        "failures": failures, "efficiency": efficiency, "judge": judge_section,
        "review": {"votes": review.vote_counts(conn, run_id),
                   "criteria": review.criteria_counts(conn, run_id)},
        "items_index": items_index,
    }
    from .summary import summarize  # local import keeps modules decoupled

    report["summary"] = summarize(report)
    return report


def _votes(data: RunData) -> dict[int, dict[str, Any]]:
    out = {}
    for b in data.baselines:
        comps = [c for c in data.comparisons if c["baseline_model_id"] == b["id"]]
        voted = [c for c in comps if c["vote"]]
        ev: dict[str, list[int]] = {"win": [], "loss": [], "tie": [], "both_bad": []}
        win_tags: Counter = Counter()
        loss_tags: Counter = Counter()
        for c in voted:
            if c["vote"] in ("tie", "both_bad"):
                ev[c["vote"]].append(c["item_id"])
                continue
            outcome = "win" if c["vote"] == c["candidate_side"] else "loss"
            ev[outcome].append(c["item_id"])
            (win_tags if outcome == "win" else loss_tags).update(c["reason_tags"])
        w, l = len(ev["win"]), len(ev["loss"])
        lo, hi = stats.wilson(w, w + l)
        p = stats.sign_test(w, l)
        out[b["id"]] = {
            "total": len(comps), "voted": len(voted),
            "counts": {k: len(v) for k, v in ev.items()},
            "win_rate": w / len(voted) if voted else None,
            "decisive_win_rate": w / (w + l) if w + l else None,
            "ci_low": lo if w + l else None, "ci_high": hi if w + l else None,
            "sign_p": p, "significant": (w + l) >= 1 and p < 0.05,
            "reasons_when_candidate_won": dict(win_tags.most_common(5)),
            "reasons_when_candidate_lost": dict(loss_tags.most_common(5)),
            "evidence": ev,
        }
    return out


def _judge_pairwise(data: RunData) -> dict[str, Any]:
    judged = [c for c in data.comparisons if c["judge_consistent"] is not None]
    consistent = [c for c in judged if c["judge_consistent"]]
    return {"judged": len(judged), "consistent": len(consistent),
            "inconsistent_items": sorted({c["item_id"] for c in judged if not c["judge_consistent"]})}


def _rubric(data: RunData, resp_by_model: dict[int, list[dict]]) -> dict[int, dict[str, Any]]:
    out = {}
    for m in data.models:
        scores, coverages, pending = [], [], []
        missed: dict[int, dict[str, Any]] = {}
        penalties: dict[int, dict[str, Any]] = {}
        excluded = 0
        for r in resp_by_model[m["id"]]:
            v = data.verdicts.get(r["id"])
            if v is None or not v.rubric:
                continue
            rb = v.rubric
            if rb["pending"]:
                pending.append(r["item_id"])
            elif rb["score"] is not None:
                scores.append(rb["score"])
                coverages.append(rb["coverage"])
            for o in rb["outcomes"]:
                if o["source"] == "excluded":
                    excluded += 1
                    continue
                if o["kind"] != "penalty":
                    entry = missed.setdefault(o["criterion_id"], {
                        "key": o["key"], "text": o["text"], "kind": o["kind"],
                        "item_id": r["item_id"], "missed": 0, "checked": 0})
                    if o["met"] is not None:
                        entry["checked"] += 1
                        entry["missed"] += int(o["met"] is False)
                elif o["met"]:
                    entry = penalties.setdefault(o["criterion_id"], {
                        "key": o["key"], "text": o["text"], "item_id": r["item_id"], "count": 0})
                    entry["count"] += 1
        top_missed = sorted((e for e in missed.values() if e["missed"]),
                            key=lambda e: (-e["missed"], e["kind"] != "must"))[:10]
        out[m["id"]] = {
            "scored": len(scores), "mean_score": stats.mean(scores),
            "mean_coverage": stats.mean(coverages), "pending_items": sorted(set(pending)),
            "excluded_criteria": excluded, "most_missed": top_missed,
            "penalties": sorted(penalties.values(), key=lambda e: -e["count"]),
        }
    return out


def _compliance(data: RunData, resp_by_model: dict[int, list[dict]]) -> dict[int, dict[str, Any]]:
    out = {}
    for m in data.models:
        passed, failed, fmt_ok, fmt_bad = [], [], [], []
        for r in resp_by_model[m["id"]]:
            item = data.items.get(r["item_id"])
            v = data.verdicts.get(r["id"])
            if item is None or v is None or v.status == "error":
                continue
            if item["grader"]["type"] == "constraints":
                (passed if v.correct else failed).append(r["item_id"])
            if "Answer:" in item["prompt"] and item["grader"]["type"] in ("exact", "numeric",
                                                                          "choice"):
                (fmt_ok if v.details.get("answer_line_present") else fmt_bad).append(r["item_id"])
        n, fn = len(passed) + len(failed), len(fmt_ok) + len(fmt_bad)
        out[m["id"]] = {
            "constraint_n": n, "constraint_rate": len(passed) / n if n else None,
            "format_n": fn, "format_rate": len(fmt_ok) / fn if fn else None,
            "evidence": {"constraint_failed": sorted(set(failed)),
                         "format_missing": sorted(set(fmt_bad))},
        }
    return out


# --------------------------------------------------------------------------
# History, release gate and exports
# --------------------------------------------------------------------------

def history(conn: sqlite3.Connection, limit: int = 50) -> list[dict[str, Any]]:
    runs = conn.execute("SELECT id FROM runs WHERE status IN ('completed', 'budget_exceeded',"
                        " 'cancelled') ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    out = []
    for row in reversed(runs):
        data = load_run(conn, row["id"])
        if data is None:
            continue
        outcomes = item_outcomes(data)
        by_cat = _by(data, "category")
        models = []
        for m in data.models:
            o = outcomes.get(m["id"], {})
            ov = _acc(o, list(data.items))
            models.append({"run_model_id": m["id"], "label": m["label"], "role": m["role"],
                           "model": m["snapshot"]["model"], "acc": ov["acc"], "n": ov["n"],
                           "ci_low": ov["ci_low"], "ci_high": ov["ci_high"],
                           "by_category": {c: _acc(o, ids)["acc"] for c, ids in by_cat.items()}})
        out.append({"run_id": data.run["id"], "name": data.run["name"],
                    "created_at": data.run["created_at"], "suite": data.version.get("suite_name"),
                    "suite_version": data.version.get("version"), "models": models})
    return out


def gate(report: dict[str, Any], baseline_model_id: int | None = None,
         max_category_drop: float | None = 5.0, min_overall: float | None = None,
         no_significant_regression: bool = True, max_unsafe: int | None = 0) -> dict[str, Any]:
    cand = report["candidate_id"]
    baselines = [m["id"] for m in report["models"] if m["role"] == "baseline"]
    base = baseline_model_id or baselines[0]
    if base not in baselines:
        raise ValueError("baseline_model_id is not a baseline in this run")
    rules = []
    if max_category_drop is not None:
        worst = []
        for cat, p in report["paired"][base]["by_category"].items():
            if p["gap_points"] is not None and -p["gap_points"] > max_category_drop:
                worst.append(f"{cat} ({p['gap_points']:+.1f} pts)")
        rules.append({"rule": f"No category drops more than {max_category_drop:g} points",
                      "passed": not worst,
                      "detail": "violations: " + ", ".join(worst) if worst else "all within limit"})
    if min_overall is not None:
        acc = report["accuracy"][cand]["overall"]["acc"]
        val = None if acc is None else acc * 100
        rules.append({"rule": f"Overall accuracy >= {min_overall:g}%",
                      "passed": val is not None and val >= min_overall,
                      "detail": "no graded items" if val is None else f"{val:.1f}%"})
    if no_significant_regression:
        sig = [c for c, p in report["paired"][base]["by_category"].items() if p["verdict"] == "worse"]
        if report["paired"][base]["overall"]["verdict"] == "worse":
            sig.append("overall")
        rules.append({"rule": "No statistically significant regression (McNemar, p<0.05)",
                      "passed": not sig,
                      "detail": "regressed: " + ", ".join(sig) if sig else "none found"})
    if max_unsafe is not None:
        n_unsafe = len(report["failures"][cand]["evidence"].get("unsafe", []))
        rules.append({"rule": f"At most {max_unsafe} unsafe responses",
                      "passed": n_unsafe <= max_unsafe, "detail": f"{n_unsafe} item(s) tagged unsafe"})
    label = next(m["label"] for m in report["models"] if m["id"] == base)
    return {"passed": all(r["passed"] for r in rules), "baseline": label, "rules": rules}


def failed_items_suite(conn: sqlite3.Connection, run_id: int) -> dict[str, Any] | None:
    """Items the candidate got wrong, in suite-import format (prompts + our own
    expected answers only; no other model's output is included)."""
    data = load_run(conn, run_id)
    if data is None:
        return None
    o = item_outcomes(data).get(data.candidate["id"], {})
    failed = {i for i, v in o.items() if v["correct"] is False}
    suite = suites.export_version(conn, data.run["suite_version_id"], failed)
    suite["name"] = f"Failures of {data.candidate['label']} (run {run_id})"[:80]
    suite["description"] = ("Items the candidate failed. Contains prompts and the suite's own "
                            "expected answers/criteria only - no model outputs.")
    return suite


def _pct(v: float | None) -> str:
    return "–" if v is None else f"{v * 100:.1f}%"


def to_markdown(report: dict[str, Any]) -> str:
    r = report["run"]
    names = {m["id"]: m["label"] for m in report["models"]}
    cand = report["candidate_id"]
    lines = [f"# Arbiter gap report: {r['name']}", "",
             f"- Run #{r['id']} · status **{r['status']}** · suite {r['suite']} v{r['suite_version']}"
             f" · {r['items']} items × {r['repetitions']} repetition(s)",
             f"- Candidate: **{names[cand]}**; baselines: "
             + ", ".join(names[m["id"]] for m in report["models"] if m["role"] == "baseline"),
             f"- Total cost: ${r['cost_usd']:.4f}", "", "## Summary", ""]
    lines += [f"- {s}" for s in report["summary"]["bullets"]]
    lines += ["", "## Accuracy by category (Wilson 95% CI, n = items)", "",
              "| Category | " + " | ".join(names[m["id"]] for m in report["models"]) + " |",
              "|---|" + "---|" * len(report["models"])]
    for cat in report["categories"] + ["__overall__"]:
        cells = []
        for m in report["models"]:
            a = (report["accuracy"][m["id"]]["overall"] if cat == "__overall__"
                 else report["accuracy"][m["id"]]["by_category"][cat])
            cells.append("–" if a["acc"] is None else
                         f"{_pct(a['acc'])} [{_pct(a['ci_low'])}, {_pct(a['ci_high'])}] n={a['n']}")
        lines.append(f"| {'**overall**' if cat == '__overall__' else cat} | " + " | ".join(cells) + " |")
    for bid, p in report["paired"].items():
        lines += ["", f"## Paired comparison vs {names[int(bid)]} (exact McNemar)", "",
                  "| Category | both right | only candidate | only baseline | both wrong | p | verdict |",
                  "|---|---|---|---|---|---|---|"]
        for cat, row in list(p["by_category"].items()) + [("overall", p["overall"])]:
            c = row["counts"]
            lines.append(f"| {cat} | {c['both_right']} | {c['only_candidate']} | {c['only_baseline']}"
                         f" | {c['both_wrong']} | {row['p_value']:.3f} | {row['label']} |")
        fl = report["flips"][bid]
        idx = report["items_index"]
        lines += ["", f"Regressions (baseline right, candidate wrong): "
                  + (", ".join(idx[i]["key"] for i in fl["regressions"]) or "none"),
                  f"Improvements (candidate right, baseline wrong): "
                  + (", ".join(idx[i]["key"] for i in fl["improvements"]) or "none")]
    for bid, v in report["votes"].items():
        if not v["voted"]:
            continue
        c = v["counts"]
        lines += ["", f"## Blind votes vs {names[int(bid)]}", "",
                  f"Win {c['win']} / tie {c['tie']} / loss {c['loss']} / both bad {c['both_bad']}"
                  f" (voted {v['voted']} of {v['total']}); sign test p={v['sign_p']:.3f}"]
    lines += ["", "## Efficiency", "", "| Model | errors | median latency | output tokens | cost | cost / correct |",
              "|---|---|---|---|---|---|"]
    for m in report["models"]:
        e = report["efficiency"][m["id"]]
        lat = "–" if e["latency_median_ms"] is None else f"{e['latency_median_ms'] / 1000:.1f}s"
        cpc = "–" if e["cost_per_correct"] is None else f"${e['cost_per_correct']:.5f}"
        lines.append(f"| {m['label']} | {e['errors']} | {lat} | {e['output_tokens']} |"
                     f" ${e['cost_usd']:.4f} | {cpc} |")
    lines += ["", "_Refusal detection is a keyword heuristic. Judge verdicts only count when "
              "calibrated against human labels._", ""]
    return "\n".join(lines)
