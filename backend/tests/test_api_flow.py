"""End-to-end API tests with the fake provider: run -> grade -> review -> report."""

import json
import logging

from arbiter.suites import SEED_PATH

from .conftest import make_endpoint

SEED = json.loads(SEED_PATH.read_text(encoding="utf-8"))
BY_PROMPT = {i["prompt"]: i for i in SEED["items"]}

GOOD_EXPLANATION = ("Hash passwords with bcrypt or Argon2 and a unique salt. Threads share the same "
                    "memory while processes have separate memory; threads are lightweight. "
                    "Overfitting means poor generalisation to unseen data; use regularisation and "
                    "dropout. Sunlight is scattered by air. TCP is reliable and uses a handshake; "
                    "UDP is connectionless and faster for video. Compound interest is interest on "
                    "interest, A = P(1 + r)^n, unlike simple interest. Public Wi-Fi traffic can be "
                    "intercepted; use a VPN and HTTPS and avoid fake hotspots.")


def answer_for(item: dict, good: bool) -> str:
    g = item["grader"]
    if g["type"] in ("numeric", "exact", "choice"):
        return f"Working...\nAnswer: {g['answer']}" if good else "Answer: 0"
    if g["type"] == "refusal":
        return "I'm sorry, but I can't help with that." if g["should_refuse"] else "Sure: here is how."
    if g["type"] == "constraints":
        return "- a\n- b\n- c"
    return GOOD_EXPLANATION


def responder(model, system, prompt):
    item = BY_PROMPT[prompt]
    # "v2" (candidate) is broken on math; "v1" (baseline) answers everything well.
    good = not (system == "v2" and item["category"] == "math")
    return answer_for(item, good)


def seed_version(client) -> int:
    return client.get("/api/suites").json()[0]["latest_version_id"]


def start_run(client, cand, base, **extra) -> dict:
    body = {"suite_version_id": seed_version(client), "candidate_id": cand["id"],
            "baseline_ids": [base["id"]], "repetitions": 1, "budget_usd": 1.0, **extra}
    r = client.post("/api/runs", json=body)
    assert r.status_code == 201, r.text
    return client.get(f"/api/runs/{r.json()['id']}").json()


def test_full_flow_report_review_gate(client, fake_llm):
    fake_llm.responder = responder
    cand = make_endpoint(client, "qwen v2", "v2")
    base = make_endpoint(client, "qwen v1", "v1")
    run = start_run(client, cand, base)
    assert run["status"] == "completed", run
    assert run["done"] == run["total"] == 61 * 2
    assert run["models"][0]["snapshot"]["system_prompt"] == "v2"  # settings snapshotted

    rep = client.get(f"/api/runs/{run['id']}/report").json()
    cid, bid = str(rep["candidate_id"]), str(next(m["id"] for m in rep["models"] if m["role"] == "baseline"))
    acc = rep["accuracy"]
    assert acc[cid]["by_category"]["math"]["acc"] == 0.0
    assert acc[bid]["by_category"]["math"]["acc"] == 1.0
    assert acc[cid]["by_category"]["math"]["n"] == 10
    math_pair = rep["paired"][bid]["by_category"]["math"]
    assert math_pair["counts"]["only_baseline"] == 10 and math_pair["verdict"] == "worse"
    assert math_pair["p_value"] < 0.01
    # Small categories must not be called significant.
    assert rep["paired"][bid]["by_category"]["safety"]["verdict"] == "insufficient"
    assert len(rep["flips"][bid]["regressions"]) == 10
    assert any("Where it lacks: math" in b for b in rep["summary"]["bullets"])
    # Evidence ids point at real math items.
    keys = {rep["items_index"][str(i)]["key"] for i in math_pair["evidence"]["only_baseline"]}
    assert all(k.startswith("math-") for k in keys)

    gate = client.post(f"/api/runs/{run['id']}/gate", json={"max_category_drop": 5}).json()
    assert gate["passed"] is False
    assert any("math" in r["detail"] for r in gate["rules"] if not r["passed"])

    # ---- Blind A/B voting: no model identity leaks -----------------------
    nxt = client.get("/api/review/votes/next", params={"run_id": run["id"]}).json()
    task = nxt["task"]
    assert task is not None and nxt["progress"]["total"] == 7
    blob = json.dumps(task).lower()
    for leak in ("qwen v1", "qwen v2", "run_model", "candidate", "baseline", "endpoint"):
        assert leak not in blob
    r = client.post(f"/api/review/votes/{task['comparison_id']}",
                    json={"vote": "left", "reason_tags": ["clearer"], "note": "ok"})
    assert r.status_code == 200
    rep = client.get(f"/api/runs/{run['id']}/report").json()
    v = rep["votes"][bid]
    assert v["voted"] == 1 and v["counts"]["win"] + v["counts"]["loss"] == 1

    # ---- Blind criteria checklist ----------------------------------------
    crit = client.get("/api/review/criteria/next", params={"run_id": run["id"]}).json()["task"]
    assert crit is not None and "qwen" not in json.dumps(crit).lower()
    labels = [{"criterion_id": c["id"], "met": c["kind"] != "penalty"} for c in crit["criteria"]]
    assert client.post(f"/api/review/criteria/{crit['response_id']}", json={"labels": labels}).status_code == 200
    bad = client.post(f"/api/review/criteria/{crit['response_id']}",
                      json={"labels": [{"criterion_id": 999999, "met": True}]})
    assert bad.status_code == 400

    # ---- Exports ----------------------------------------------------------
    md = client.get(f"/api/runs/{run['id']}/report.md")
    assert md.status_code == 200 and "# Arbiter gap report" in md.text
    assert "attachment" in md.headers["content-disposition"]
    failed = client.get(f"/api/runs/{run['id']}/failed-items").json()
    assert {i["key"] for i in failed["items"]} >= {f"math-{n:02d}" for n in range(1, 11)}
    assert "Answer: 0" not in json.dumps(failed)  # no model outputs in the export

    hist = client.get("/api/history").json()
    assert hist[-1]["run_id"] == run["id"] and len(hist[-1]["models"]) == 2


def test_hidden_criteria_and_answers_never_sent_to_models(client, fake_llm):
    fake_llm.responder = responder
    cand = make_endpoint(client, "cand", "v2")
    base = make_endpoint(client, "base", "v1")
    run = start_run(client, cand, base)
    assert run["status"] == "completed"
    sent = "\n".join(fake_llm.bodies())
    assert len(fake_llm.requests) == 122
    secret_bits = []
    for item in SEED["items"]:
        for c in item.get("criteria", []):
            secret_bits.append(c["text"])
            secret_bits.extend(c.get("keywords", []) if len(c.get("keywords", [])) > 2 else [])
            if c.get("pattern"):
                secret_bits.append(c["pattern"])
        if item.get("notes"):
            secret_bits.append(item["notes"])
    for bit in secret_bits:
        assert json.dumps(bit)[1:-1] not in sent, f"leaked: {bit}"


def test_api_key_never_returned_or_logged(client, fake_llm, caplog):
    ep = make_endpoint(client, "gpt", provider="openai", model="gpt-4o-mini")
    secret = "sk-test-SYNTHETIC-not-a-real-key-1234"
    caplog.set_level(logging.DEBUG)
    r = client.put(f"/api/endpoints/{ep['id']}/key", json={"api_key": secret})
    assert r.status_code == 200 and r.json() == {"has_key": True, "masked": "••••1234",
                                                 "store_error": False}
    listing = client.get("/api/endpoints").text
    assert secret not in listing and "••••1234" in listing
    assert client.post(f"/api/endpoints/{ep['id']}/test").json()["ok"] is True
    assert secret not in caplog.text
    # A validation error must not echo the submitted value back.
    bad = client.put(f"/api/endpoints/{ep['id']}/key", json={"api_key": "has space secret"})
    assert bad.status_code == 422 and "has space secret" not in bad.text


def test_run_refused_without_key_and_shows_destination(client, fake_llm):
    cand = make_endpoint(client, "local", "v2")
    paid = make_endpoint(client, "claude", provider="anthropic", model="claude-haiku-4-5",
                         price_in=1, price_out=5)
    body = {"suite_version_id": seed_version(client), "candidate_id": cand["id"],
            "baseline_ids": [paid["id"]]}
    est = client.post("/api/runs/estimate", json=body).json()
    dest = {e["name"]: e for e in est["endpoints"]}
    assert dest["local"]["local"] is True and dest["claude"]["company"] == "Anthropic"
    assert dest["claude"]["host"] == "api.anthropic.com" and dest["claude"]["key_missing"]
    assert est["total_max"] > 0
    r = client.post("/api/runs", json=body)
    assert r.status_code == 400 and "Missing API key" in r.text


def test_budget_cap_stops_run(client, fake_llm):
    fake_llm.responder = responder
    cand = make_endpoint(client, "local", "v2")
    paid = make_endpoint(client, "gpt", provider="openai", model="gpt-4o", max_tokens=1000,
                         price_in=1000, price_out=1000)
    client.put(f"/api/endpoints/{paid['id']}/key", json={"api_key": "sk-test-SYNTHETIC-000000"})
    run = start_run(client, cand, paid, budget_usd=0.5)
    assert run["status"] == "budget_exceeded" and "budget" in run["error"]
    assert run["cost_usd"] <= 0.5
    assert run["done"] < run["total"]


def test_response_cache_and_repetitions(client, fake_llm):
    fake_llm.responder = responder
    cand = make_endpoint(client, "cand", "v2")
    base = make_endpoint(client, "base", "v1")
    vid = seed_version(client)
    items = client.get(f"/api/suite-versions/{vid}").json()["items"][:3]
    ids = [i["id"] for i in items]
    run1 = start_run(client, cand, base, repetitions=2, item_ids=ids)
    assert run1["done"] == 3 * 2 * 2
    n_requests = len(fake_llm.requests)
    run2 = start_run(client, cand, base, repetitions=2, item_ids=ids)
    assert run2["status"] == "completed" and len(fake_llm.requests) == n_requests  # all cached
    rep = client.get(f"/api/runs/{run2['id']}/report").json()
    eff = rep["efficiency"][str(rep["candidate_id"])]
    assert eff["cached"] == 6 and eff["cost_usd"] == 0
    cons = rep["consistency"][str(rep["candidate_id"])]
    assert cons["n"] == 3 and cons["rate"] == 1.0


def test_ollama_offline_gives_per_response_errors(client, fake_llm):
    fake_llm.offline = True
    cand = make_endpoint(client, "cand", "v2")
    base = make_endpoint(client, "base", "v1")
    vid = seed_version(client)
    ids = [i["id"] for i in client.get(f"/api/suite-versions/{vid}").json()["items"][:2]]
    run = start_run(client, cand, base, item_ids=ids)
    assert run["status"] == "completed" and run["errors"] == 4
    items = client.get(f"/api/runs/{run['id']}/items").json()
    resp = items[0]["responses"][0]
    assert resp["status"] == "error" and "ollama serve" in resp["error"]
    assert resp["verdict"]["status"] == "error"
    health = client.get("/api/health").json()
    assert health["ollama"]["reachable"] is False


def test_tags_feed_failure_taxonomy(client, fake_llm):
    fake_llm.responder = responder
    cand = make_endpoint(client, "cand", "v2")
    base = make_endpoint(client, "base", "v1")
    vid = seed_version(client)
    ids = [i["id"] for i in client.get(f"/api/suite-versions/{vid}").json()["items"][:1]]
    run = start_run(client, cand, base, item_ids=ids)
    items = client.get(f"/api/runs/{run['id']}/items").json()
    cand_resp = next(r for r in items[0]["responses"] if r["run_model_id"] == run["models"][0]["id"])
    r = client.put(f"/api/responses/{cand_resp['id']}/tags", json={"tags": ["arithmetic_error"]})
    assert r.json() == {"tags": ["arithmetic_error"]}
    rep = client.get(f"/api/runs/{run['id']}/report").json()
    assert rep["failures"][str(rep["candidate_id"])]["counts"] == {"arithmetic_error": 1}
    assert client.put(f"/api/responses/{cand_resp['id']}/tags", json={"tags": ["bogus"]}).status_code == 422


def test_security_headers_and_cors(client):
    r = client.get("/api/health", headers={"Origin": "http://localhost:5173"})
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["access-control-allow-origin"] == "http://localhost:5173"
    evil = client.get("/api/health", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in evil.headers
    big = client.post("/api/suites", content=b"x", headers={"content-length": str(50 * 1024 * 1024),
                                                             "content-type": "application/json"})
    assert big.status_code == 413


def test_truncation_reported_and_inferred_for_legacy_rows(client, fake_llm):
    fake_llm.responder = responder
    cand = make_endpoint(client, "cand", "v2", max_tokens=10)  # fake always reports 10 output tokens
    base = make_endpoint(client, "base", "v1", max_tokens=500)
    vid = seed_version(client)
    ids = [i["id"] for i in client.get(f"/api/suite-versions/{vid}").json()["items"][:2]]
    run = start_run(client, cand, base, item_ids=ids)
    from arbiter import db as _db
    with _db.session() as conn:  # simulate rows written before the column existed
        conn.execute("UPDATE responses SET truncated = NULL WHERE run_id = ?", (run["id"],))
    rep = client.get(f"/api/runs/{run['id']}/report").json()
    assert rep["efficiency"][str(rep["candidate_id"])]["truncated"] == 2
    base_id = next(m["id"] for m in rep["models"] if m["role"] == "baseline")
    assert rep["efficiency"][str(base_id)]["truncated"] == 0
    assert any("max-token limit" in b for b in rep["summary"]["bullets"])


def test_inline_reasoning_hidden_from_graders_and_blind_reviewers(client, fake_llm):
    """qwen3-style output: reasoning inline, closed by a lone </think>."""
    def resp(model, system, prompt):
        if system == "v2":  # candidate: reasoning mentions its system prompt, then answers
            return ("My instructions say keep it under 60 words. hash salt bcrypt\n</think>\n\n"
                    "Use Argon2.")
        if "passwords" in prompt:  # baseline: cut off mid-reasoning (no </think>)
            return "Okay, I should mention hashing with bcrypt and a salt, and"
        return "Fine.\n</think>\nAnswer: 1"
    fake_llm.responder = resp
    fake_llm.truncate = lambda m, s, p: s == "v1" and "passwords" in p
    cand = make_endpoint(client, "cand", "v2")
    base = make_endpoint(client, "base", "v1")
    vid = seed_version(client)
    items = client.get(f"/api/suite-versions/{vid}").json()["items"]
    ids = [i["id"] for i in items if i["key"] in ("explain-01", "math-01")]
    run = start_run(client, cand, base, item_ids=ids)
    rows = {(i["key"], r["run_model_id"]): r for i in client.get(f"/api/runs/{run['id']}/items").json()
            for r in i["responses"]}
    c_id, b_id = run["models"][0]["id"], run["models"][1]["id"]
    cand_v = rows[("explain-01", c_id)]["verdict"]
    met = {o["key"]: o["met"] for o in cand_v["rubric"]["outcomes"]}
    assert met["hashing"] is False and met["salting"] is False  # keywords only in reasoning
    assert met["slow_kdf"] is True  # "Argon2" is in the final answer
    base_v = rows[("explain-01", b_id)]["verdict"]
    assert base_v["status"] == "incorrect" and base_v["rubric"] is None
    task = client.get("/api/review/votes/next", params={"run_id": run["id"]}).json()["task"]
    shown = task["a_text"] + task["b_text"]
    assert "60 words" not in shown and "Use Argon2." in shown and "No final answer" in shown
    crit = client.get("/api/review/criteria/next", params={"run_id": run["id"]}).json()
    assert crit["progress"]["total"] == 1  # the cut-off response needs no human review
    assert "60 words" not in crit["task"]["response_text"]
