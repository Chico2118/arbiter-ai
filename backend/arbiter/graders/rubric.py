"""Hidden-rubric scoring.

Criteria are atomic statements a good answer should (must / nice) or should
not (penalty) satisfy. They are stored separately from prompts and are never
part of any request to a model under test.

Score = (weighted coverage of must + nice criteria - weighted penalties
triggered) / total positive weight, clipped to [0, 1]. A missed must-have caps
the score at ``must_cap`` (default 0.5), so a fluent answer that omits the
essential point cannot pass.

How each criterion is resolved:
  keywords / regex -> automatic, from the response text
  human            -> a blind human checkbox label (pending until labelled)
  judge            -> a human label if one exists; otherwise the AI judge's
                      verdict, but only when that criterion's judge is
                      calibrated. Uncalibrated judge criteria are excluded.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .text import contains_phrase, strip_thinking


def auto_check(criterion: dict, text: str) -> tuple[bool, str] | None:
    """Evaluate keyword/regex criteria. Returns None for human/judge criteria."""
    cfg = criterion.get("config") or {}
    ctype = criterion["check_type"]
    if ctype == "keywords":
        words = cfg.get("keywords", [])
        hits = [w for w in words if contains_phrase(text, w)]
        met = bool(hits) if cfg.get("match", "any") == "any" else len(hits) == len(words)
        return met, f"matched: {', '.join(hits)}" if hits else "no keyword matched"
    if ctype == "regex":
        m = re.search(cfg.get("pattern", ""), strip_thinking(text), re.IGNORECASE)
        return m is not None, f"matched: {m.group(0)[:80]}" if m else "pattern not found"
    return None


@dataclass
class CriterionOutcome:
    criterion_id: int
    key: str
    text: str
    kind: str
    weight: float
    check_type: str
    met: bool | None
    source: str  # auto | human | judge | pending | excluded
    note: str = ""
    failure_tag: str | None = None


@dataclass
class RubricResult:
    score: float | None
    pending: bool
    coverage: float | None
    must_missed: bool
    penalties: list[str] = field(default_factory=list)
    outcomes: list[CriterionOutcome] = field(default_factory=list)


def resolve(
    criteria: list[dict],
    text: str,
    labels: dict[tuple[int, str], dict[str, Any]],
    calibrated_keys: set[str],
) -> list[CriterionOutcome]:
    """Resolve every criterion for one response.

    ``labels`` maps (criterion_id, source) -> {"met": bool|None, "evidence": {...}}.
    """
    out = []
    for c in criteria:
        base = dict(criterion_id=c["id"], key=c["key"], text=c["text"], kind=c["kind"],
                    weight=float(c["weight"]), check_type=c["check_type"],
                    failure_tag=c.get("failure_tag"))
        auto = auto_check(c, text)
        human = labels.get((c["id"], "human"))
        judge = labels.get((c["id"], "judge"))
        if auto is not None:
            out.append(CriterionOutcome(met=auto[0], source="auto", note=auto[1], **base))
        elif human is not None and human.get("met") is not None:
            out.append(CriterionOutcome(met=bool(human["met"]), source="human", **base))
        elif c["check_type"] == "human":
            out.append(CriterionOutcome(met=None, source="pending",
                                        note="awaiting blind human review", **base))
        elif judge is not None and judge.get("met") is not None and c["key"] in calibrated_keys:
            quote = (judge.get("evidence") or {}).get("quote", "")
            out.append(CriterionOutcome(met=bool(judge["met"]), source="judge",
                                        note=f"judge quote: {quote[:120]}", **base))
        else:
            reason = "judge uncalibrated" if judge is not None else "not judged"
            out.append(CriterionOutcome(met=None, source="excluded",
                                        note=f"excluded ({reason})", **base))
    return out


def score(outcomes: list[CriterionOutcome], must_cap: float = 0.5) -> RubricResult:
    counted = [o for o in outcomes if o.source != "excluded"]
    pending = any(o.met is None for o in counted)
    resolved = [o for o in counted if o.met is not None]
    positives = [o for o in counted if o.kind in ("must", "nice")]
    total_w = sum(o.weight for o in positives)
    if total_w == 0:
        return RubricResult(None, pending, None, False, [], outcomes)
    covered = sum(o.weight for o in resolved if o.kind in ("must", "nice") and o.met)
    triggered = [o for o in resolved if o.kind == "penalty" and o.met]
    penalty_w = sum(o.weight for o in triggered)
    must_missed = any(o.kind == "must" and o.met is False for o in resolved)
    value = max(0.0, min(1.0, (covered - penalty_w) / total_w))
    if must_missed:
        value = min(value, must_cap)
    return RubricResult(
        score=round(value, 4),
        pending=pending,
        coverage=round(covered / total_w, 4),
        must_missed=must_missed,
        penalties=[o.key for o in triggered],
        outcomes=outcomes,
    )
