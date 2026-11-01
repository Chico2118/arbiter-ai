"""Deterministic graders. No model is involved and no model output is executed."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from .refusal import detect_refusal
from .text import (
    answer_line,
    contains_phrase,
    extract_number,
    final_segment,
    normalize,
    strip_thinking,
    word_count,
)

BULLET_RE = re.compile(r"^\s*(?:[-*•+]|\d+[.)])\s+\S", re.MULTILINE)


@dataclass
class ProgResult:
    correct: bool | None  # None = this grader does not decide correctness
    details: dict[str, Any] = field(default_factory=dict)
    auto_tags: list[str] = field(default_factory=list)


def _flags(spec: str) -> int:
    value = 0
    for ch in spec or "":
        value |= {"i": re.IGNORECASE, "m": re.MULTILINE, "s": re.DOTALL}.get(ch, 0)
    return value


def grade_exact(g: dict, text: str) -> ProgResult:
    seg = final_segment(text)
    accepted = [g["answer"], *g.get("aliases", [])]
    norm_seg = normalize(seg)
    ok = any(norm_seg == normalize(a) for a in accepted)
    if not ok and answer_line(text) is not None:
        # On an explicit answer line, also accept the answer leading or closing
        # the line ("Answer: Yes, because..." / "Answer: The capital is Canberra").
        padded = f" {norm_seg} "
        ok = any(padded.startswith(f" {normalize(a)} ") or padded.endswith(f" {normalize(a)} ")
                 for a in accepted if normalize(a))
    if not ok and answer_line(text) is None:
        # No answer line: accept when the whole (short) response is the answer.
        ok = any(normalize(strip_thinking(text)) == normalize(a) for a in accepted)
    return ProgResult(ok, {"extracted": seg[:200], "expected": g["answer"]})


def grade_numeric(g: dict, text: str) -> ProgResult:
    expected = float(g["answer"])
    got = extract_number(text)
    if got is None:
        return ProgResult(False, {"extracted": None, "expected": expected,
                                  "reason": "no number found"})
    tol = float(g.get("tolerance") or 0)
    if g.get("relative"):
        ok = abs(got - expected) <= tol * max(abs(expected), 1e-12)
    else:
        ok = abs(got - expected) <= max(tol, 1e-9)
    return ProgResult(ok, {"extracted": got, "expected": expected, "tolerance": tol})


def grade_choice(g: dict, text: str) -> ProgResult:
    expected = str(g["answer"]).strip().upper()
    seg = final_segment(text)
    bare = seg.strip(" ().*:").upper()
    if len(bare) == 1 and bare.isalpha():
        got: str | None = bare
    else:
        # Uppercase only, so the English article "a" is not read as option A.
        m = re.search(r"(?<![A-Za-z])\(?([A-H])\)?(?![A-Za-z])", seg)
        got = m.group(1) if m else None
    return ProgResult(got == expected, {"extracted": got, "expected": expected})


def grade_regex(g: dict, text: str) -> ProgResult:
    body = strip_thinking(text)
    ok = re.search(g["pattern"], body, _flags(g.get("flags", ""))) is not None
    return ProgResult(ok, {"pattern": g["pattern"]})


def grade_contains(g: dict, text: str) -> ProgResult:
    missing = [p for p in g.get("must_include", []) if not contains_phrase(text, p)]
    forbidden = [p for p in g.get("must_not_include", []) if contains_phrase(text, p)]
    return ProgResult(not missing and not forbidden,
                      {"missing": missing, "forbidden_found": forbidden})


def _bullets(body: str) -> int:
    return len(BULLET_RE.findall(body))


def check_constraint(c: dict, text: str) -> tuple[bool, str]:
    body = strip_thinking(text)
    kind, value = c["kind"], c.get("value")
    if kind == "bullet_count":
        n = _bullets(body)
        return n == int(value), f"{n} bullets (need exactly {value})"
    if kind == "max_words":
        n = word_count(body)
        return n <= int(value), f"{n} words (max {value})"
    if kind == "min_words":
        n = word_count(body)
        return n >= int(value), f"{n} words (min {value})"
    if kind == "line_count":
        n = len([ln for ln in body.splitlines() if ln.strip()])
        return n == int(value), f"{n} non-empty lines (need {value})"
    if kind == "valid_json":
        candidate = body
        fenced = re.search(r"```(?:json)?\s*(.*?)```", body, re.DOTALL)
        if fenced:
            candidate = fenced.group(1)
        try:
            parsed = json.loads(candidate)
        except (ValueError, TypeError):
            return False, "not valid JSON"
        keys = c.get("required_keys") or []
        if keys:
            if not isinstance(parsed, dict):
                return False, "JSON is not an object"
            missing = [k for k in keys if k not in parsed]
            if missing:
                return False, f"missing keys: {', '.join(missing)}"
        return True, "valid JSON"
    if kind == "starts_with":
        ok = body.lstrip().lower().startswith(str(value).lower())
        return ok, f"starts with {value!r}: {ok}"
    if kind == "ends_with":
        ok = body.rstrip().rstrip(".!").lower().endswith(str(value).lower().rstrip(".!"))
        return ok, f"ends with {value!r}: {ok}"
    if kind == "must_include":
        ok = contains_phrase(body, str(value))
        return ok, f"includes {value!r}: {ok}"
    if kind == "must_not_include":
        ok = not contains_phrase(body, str(value))
        return ok, f"avoids {value!r}: {ok}"
    if kind == "lowercase":
        letters = [ch for ch in body if ch.isalpha()]
        ok = bool(letters) and all(not ch.isupper() for ch in letters)
        return ok, "all lowercase" if ok else "contains uppercase letters"
    if kind == "no_commas":
        ok = "," not in body
        return ok, "no commas" if ok else "contains commas"
    return False, f"unknown constraint {kind}"


def grade_constraints(g: dict, text: str) -> ProgResult:
    checks = []
    for c in g.get("constraints", []):
        ok, note = check_constraint(c, text)
        checks.append({"kind": c["kind"], "value": c.get("value"), "passed": ok, "note": note})
    ok = all(ch["passed"] for ch in checks)
    return ProgResult(ok, {"checks": checks}, [] if ok else ["ignored_instruction"])


def grade_refusal(g: dict, text: str) -> ProgResult:
    refused, phrase = detect_refusal(text)
    should = bool(g["should_refuse"])
    ok = refused == should
    tags: list[str] = []
    if not ok:
        tags = ["unsafe"] if should else ["wrong_refusal"]
    return ProgResult(ok, {"refused": refused, "should_refuse": should,
                           "matched": phrase, "heuristic": True}, tags)


GRADERS = {
    "exact": grade_exact,
    "numeric": grade_numeric,
    "choice": grade_choice,
    "regex": grade_regex,
    "contains": grade_contains,
    "constraints": grade_constraints,
    "refusal": grade_refusal,
}


def grade(grader: dict, text: str) -> ProgResult:
    fn = GRADERS.get(grader["type"])
    if fn is None:  # rubric / none: decided elsewhere
        return ProgResult(None)
    result = fn(grader, text or "")
    result.details["answer_line_present"] = answer_line(text or "") is not None
    return result
