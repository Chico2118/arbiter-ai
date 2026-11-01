"""Plain-language summary built deterministically from report numbers (no AI).

Wording rules: "lacks"/"better" are used only for statistically significant
differences (exact McNemar p < 0.05 with enough paired items). Everything else
is reported as "no significant difference" or "not enough data".
"""

from __future__ import annotations

from typing import Any

TAG_LABELS = {
    "hallucination": "hallucination", "arithmetic_error": "arithmetic errors",
    "wrong_reasoning": "wrong reasoning steps", "ignored_instruction": "ignored instructions",
    "wrong_refusal": "refusing safe requests", "unsafe": "unsafe compliance",
}


def _pts(v: float | None) -> str:
    return "n/a" if v is None else f"{v:+.1f} pts"


def summarize(report: dict[str, Any]) -> dict[str, Any]:
    names = {m["id"]: m["label"] for m in report["models"]}
    cand = report["candidate_id"]
    bullets: list[str] = []
    lacks, matches, better, thin = [], [], [], []

    for bid, p in report["paired"].items():
        bname = names[bid]
        ov = p["overall"]
        if ov["n"]:
            bullets.append(
                f"Overall vs {bname}: candidate {ov['candidate_acc'] * 100:.1f}% vs "
                f"{ov['baseline_acc'] * 100:.1f}% on {ov['n']} paired items "
                f"({_pts(ov['gap_points'])}); {ov['label']}.")
        for cat, row in p["by_category"].items():
            entry = (cat, bname, row)
            {"worse": lacks, "better": better, "no_difference": matches,
             "insufficient": thin}[row["verdict"]].append(entry)

    if lacks:
        bullets.append("Where it lacks: " + "; ".join(
            f"{c} vs {b} ({_pts(r['gap_points'])}, p={r['p_value']:.3f})" for c, b, r in lacks) + ".")
    else:
        bullets.append("Where it lacks: no category shows a statistically significant deficit.")
    if better:
        bullets.append("Where it is better: " + "; ".join(
            f"{c} vs {b} ({_pts(r['gap_points'])}, p={r['p_value']:.3f})" for c, b, r in better) + ".")
    if matches:
        bullets.append("No significant difference: " + ", ".join(
            sorted({f"{c} vs {b}" for c, b, _ in matches})) + ".")
    if thin:
        bullets.append("Too few paired items to conclude (n < "
                       f"{report['min_paired_n']}): " + ", ".join(
                           sorted({f"{c} vs {b} ({_pts(r['gap_points'])}, n={r['n']})"
                                   for c, b, r in thin})) + ".")

    for bid, v in report["votes"].items():
        if v["voted"]:
            c = v["counts"]
            sig = "significant" if v["significant"] else "not significant"
            bullets.append(f"Blind votes vs {names[bid]}: {c['win']} wins, {c['tie']} ties, "
                           f"{c['loss']} losses, {c['both_bad']} both bad (sign test "
                           f"p={v['sign_p']:.3f}, {sig}).")
        elif v["total"]:
            bullets.append(f"Blind votes vs {names[bid]}: {v['total']} pairs awaiting review.")

    # Suggested focus: the largest negative gap (any baseline), top failure tag,
    # most-missed hidden criterion.
    focus: list[str] = []
    gaps = [(row["gap_points"], cat, names[bid]) for bid, p in report["paired"].items()
            for cat, row in p["by_category"].items() if row["gap_points"] is not None]
    gaps = [g for g in gaps if g[0] < 0]
    if gaps:
        g = min(gaps)
        focus.append(f"{g[1]} (largest gap: {g[0]:+.1f} pts vs {g[2]})")
    fails = report["failures"][cand]["counts"]
    if fails:
        tag, n = next(iter(fails.items()))
        focus.append(f"{TAG_LABELS.get(tag, tag)} ({n} response(s))")
    missed = report["rubric"][cand]["most_missed"]
    if missed:
        focus.append(f"hidden criterion \"{missed[0]['text']}\" missed {missed[0]['missed']}x")
    if focus:
        bullets.append("Suggested focus: " + "; ".join(focus) + ".")

    for m in report["models"]:
        n_trunc = report["efficiency"][m["id"]].get("truncated", 0)
        if n_trunc:
            bullets.append(f"{m['label']}: {n_trunc} response(s) hit the max-token limit and were cut "
                           "off; some 'incorrect' verdicts may be truncation, not wrong answers. "
                           "Consider raising max tokens.")

    pending_v = report["review"]["votes"]["total"] - report["review"]["votes"]["done"]
    pending_c = report["review"]["criteria"]["total"] - report["review"]["criteria"]["done"]
    if pending_v or pending_c:
        bullets.append(f"Review pending: {pending_v} blind vote(s) and {pending_c} criterion "
                       "checklist(s); rubric scores for those items are incomplete.")

    return {"bullets": bullets,
            "lacks": [{"category": c, "baseline": b} for c, b, _ in lacks],
            "better": [{"category": c, "baseline": b} for c, b, _ in better],
            "matches": [{"category": c, "baseline": b} for c, b, _ in matches]}
