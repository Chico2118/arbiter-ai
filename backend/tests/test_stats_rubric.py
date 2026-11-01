import math

import pytest

from arbiter import graders, stats
from arbiter.graders import rubric


def test_wilson_known_values():
    lo, hi = stats.wilson(8, 10)
    # Reference values for the Wilson 95% interval at 8/10.
    assert lo == pytest.approx(0.4902, abs=1e-3)
    assert hi == pytest.approx(0.9433, abs=1e-3)
    assert stats.wilson(0, 0) == (0.0, 1.0)
    lo0, hi0 = stats.wilson(0, 5)
    assert lo0 == 0.0 and 0 < hi0 < 0.6


def test_binomial_and_mcnemar():
    # 1 vs 9 discordant pairs: p = 2 * (C(10,0)+C(10,1)) / 2^10 = 22/1024
    assert stats.mcnemar_exact(1, 9) == pytest.approx(22 / 1024)
    assert stats.mcnemar_exact(0, 0) == 1.0
    assert stats.mcnemar_exact(5, 5) == 1.0
    assert stats.sign_test(10, 0) == pytest.approx(2 / 1024)


def test_cohen_kappa():
    a = [True, True, False, False, True, False]
    assert stats.cohen_kappa(a, a) == 1.0
    assert stats.cohen_kappa(a, [not x for x in a]) == pytest.approx(-1.0)
    # 4/6 agreement, pa = pb = 0.5 -> pe = 0.5 -> kappa = (0.667 - 0.5) / 0.5
    b = [True, False, False, True, True, False]
    assert stats.cohen_kappa(a, b) == pytest.approx((4 / 6 - 0.5) / 0.5)
    assert stats.cohen_kappa([], []) is None
    with pytest.raises(ValueError):
        stats.cohen_kappa([True], [])


def _crit(cid, key, kind, check_type, weight=1.0, **cfg):
    return {"id": cid, "key": key, "text": key, "kind": kind, "weight": weight,
            "check_type": check_type, "config": cfg}


CRITERIA = [
    _crit(1, "hash", "must", "keywords", 2, keywords=["hash", "hashed"]),
    _crit(2, "kdf", "must", "regex", 2, pattern="bcrypt|argon2"),
    _crit(3, "salt", "nice", "keywords", 1, keywords=["salt"]),
    _crit(4, "plain", "penalty", "human", 2),
]


def test_rubric_full_marks_and_penalty():
    text = "Hash with bcrypt and a unique salt."
    out = rubric.resolve(CRITERIA, text, {(4, "human"): {"met": 0}}, set())
    r = rubric.score(out)
    assert r.score == 1.0 and not r.pending
    out = rubric.resolve(CRITERIA, text, {(4, "human"): {"met": 1}}, set())
    r = rubric.score(out)
    assert r.score == pytest.approx((5 - 2) / 5) and r.penalties == ["plain"]


def test_missed_must_caps_score():
    text = "Hash passwords and add salt."  # no bcrypt/argon2
    out = rubric.resolve(CRITERIA, text, {(4, "human"): {"met": 0}}, set())
    r = rubric.score(out, must_cap=0.5)
    assert r.must_missed and r.score == 0.5  # coverage 3/5 = 0.6 capped at 0.5


def test_human_criterion_pending_until_labelled():
    out = rubric.resolve(CRITERIA, "hash bcrypt salt", {}, set())
    assert rubric.score(out).pending


def test_judge_criterion_only_counts_when_calibrated():
    crit = [_crit(1, "hash", "must", "keywords", keywords=["hash"]),
            _crit(2, "explains_why", "nice", "judge")]
    labels = {(2, "judge"): {"met": 1, "evidence": {"quote": "hash"}}}
    uncal = rubric.resolve(crit, "hash it", labels, set())
    assert uncal[1].source == "excluded"
    assert rubric.score(uncal).score == 1.0  # excluded from the denominator
    cal = rubric.resolve(crit, "hash it", labels, {"explains_why"})
    assert cal[1].source == "judge" and cal[1].met is True
    # A human label always wins over the judge.
    human = rubric.resolve(crit, "hash it", labels | {(2, "human"): {"met": 0}}, {"explains_why"})
    assert human[1].source == "human" and human[1].met is False


def test_evaluate_rubric_item_threshold_and_tags():
    item = {"grader": {"type": "rubric", "pass_threshold": 0.7, "must_cap": 0.5}}
    crit = CRITERIA[:3] + [dict(CRITERIA[3], failure_tag="unsafe")]
    v = graders.evaluate(item, crit, {"status": "ok", "text": "hash, bcrypt, salt"},
                         {(4, "human"): {"met": 1}})
    assert v.method == "rubric" and v.correct is False and "unsafe" in v.auto_tags
    v2 = graders.evaluate(item, crit, {"status": "ok", "text": "hash, bcrypt, salt"},
                          {(4, "human"): {"met": 0}})
    assert v2.correct is True and math.isclose(v2.rubric["score"], 1.0)
