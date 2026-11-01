import json

from arbiter import cli, db, judge, providers

from .conftest import make_endpoint

ITEM = {"key": "q1", "category": "math", "difficulty": "easy", "prompt": "1+1? Answer: <n>",
        "grader": {"type": "numeric", "answer": 2}}


def _suite(client, name="Mini", items=None) -> dict:
    r = client.post("/api/suites", json={"name": name, "items": items or [ITEM]})
    assert r.status_code == 201, r.text
    return r.json()


def test_versions_lock_when_run_starts_and_edits_fork(client, fake_llm):
    fake_llm.responder = lambda m, s, p: "Answer: 2"
    ids = _suite(client)
    a, b = make_endpoint(client, "a", "x"), make_endpoint(client, "b", "y")
    vid = ids["version_id"]
    # Unlocked: edits apply in place.
    r = client.put(f"/api/suite-versions/{vid}/items", json={**ITEM, "prompt": "2+0? Answer: <n>"})
    assert r.json()["new_version"] is False
    client.post("/api/runs", json={"suite_version_id": vid, "candidate_id": a["id"],
                                   "baseline_ids": [b["id"]]})
    assert client.get(f"/api/suite-versions/{vid}").json()["version"]["locked"] == 1
    # Locked: an edit creates v2 and leaves v1 untouched.
    r = client.put(f"/api/suite-versions/{vid}/items", json={**ITEM, "key": "q2"}).json()
    assert r["new_version"] is True and r["version_id"] != vid
    v1 = client.get(f"/api/suite-versions/{vid}").json()
    v2 = client.get(f"/api/suite-versions/{r['version_id']}").json()
    assert [i["key"] for i in v1["items"]] == ["q1"]
    assert [i["key"] for i in v2["items"]] == ["q1", "q2"] and v2["version"]["version"] == 2
    # Editing the stale v1 again is refused while v2 is the open draft.
    assert client.put(f"/api/suite-versions/{vid}/items", json=ITEM).status_code == 409


def test_import_export_roundtrip_and_validation(client):
    seed_vid = client.get("/api/suites").json()[0]["latest_version_id"]
    exported = client.get(f"/api/suite-versions/{seed_vid}/export")
    assert exported.headers["content-disposition"].startswith("attachment")
    data = exported.json()
    data["name"] = "Copy"
    r = client.post("/api/suites/import", json={"filename": "copy.json", "suite": data})
    assert r.status_code == 201, r.text
    copy = client.get(f"/api/suite-versions/{r.json()['version_id']}").json()
    assert len(copy["items"]) == 61
    crit = [c for i in copy["items"] for c in i["criteria"]]
    assert len(crit) > 20 and {c["check_type"] for c in crit} == {"keywords", "regex", "human", "judge"}
    # Unsafe filenames, bad graders and duplicate keys are rejected.
    for fname in ("../evil.json", "a/b.json", "x.exe"):
        bad = client.post("/api/suites/import", json={"filename": fname, "suite": {"name": "Z", "items": []}})
        assert bad.status_code == 422
    bad = client.post("/api/suites", json={"name": "B", "items": [
        {**ITEM, "grader": {"type": "regex", "pattern": "("}}]})
    assert bad.status_code == 422
    dup = client.post("/api/suites", json={"name": "C", "items": [ITEM, ITEM]})
    assert dup.status_code == 422
    rubric_no_criteria = client.post("/api/suites", json={"name": "D", "items": [
        {**ITEM, "grader": {"type": "rubric"}}]})
    assert rubric_no_criteria.status_code == 422


OPEN_ITEM = {"key": "o1", "category": "explanation", "difficulty": "easy", "open_ended": True,
             "prompt": "Explain hashing.", "grader": {"type": "rubric"},
             "criteria": [{"key": "mentions_salt", "text": "Mentions salt", "kind": "must",
                           "check_type": "judge"}]}


def _judge_responder(model, system, prompt):
    if "grading one response" in prompt:
        # Always cites the same quote: real for the "good" answer, fabricated
        # for the "bad" one (which never mentions salt).
        return '{"verdict": "yes", "quote": "with a unique salt"}'
    if "Which is better" in prompt:
        # Always prefers whatever is shown as A -> position-biased, inconsistent.
        return '{"winner": "A"}'
    return "Store a hash with a unique salt." if system == "good" else "Just use md5."


def test_judge_quote_verification_family_and_pairwise(client, fake_llm):
    fake_llm.responder = _judge_responder
    ids = _suite(client, "Judge", [OPEN_ITEM])
    good = make_endpoint(client, "good", "good")
    bad = make_endpoint(client, "bad", "bad", model="llama3.2:3b")
    jdg = make_endpoint(client, "judge", "", provider="openai_compatible", model="mistral-small",
                        base_url="http://localhost:9000/v1")
    body = {"suite_version_id": ids["version_id"], "candidate_id": good["id"],
            "baseline_ids": [bad["id"]], "judge": {"enabled": True, "endpoint_id": jdg["id"],
                                                   "pairwise": True}}
    est = client.post("/api/runs/estimate", json=body).json()
    assert est["warnings"] == []
    run_id = client.post("/api/runs", json=body).json()["id"]
    assert client.get(f"/api/runs/{run_id}").json()["status"] == "completed"
    with db.session() as conn:
        rows = conn.execute("SELECT r.text, c.met, c.evidence FROM criterion_results c JOIN responses r"
                            " ON r.id = c.response_id WHERE c.source = 'judge'").fetchall()
        comp = conn.execute("SELECT * FROM comparisons").fetchone()
    by_text = {r["text"]: (r["met"], json.loads(r["evidence"])) for r in rows}
    assert by_text["Store a hash with a unique salt."][0] == 1
    met, ev = by_text["Just use md5."]
    assert met is None and ev["quote_verified"] is False  # fabricated quote discarded
    assert comp["judge_consistent"] == 0  # position bias detected by the swap
    rep = client.get(f"/api/runs/{run_id}/report").json()
    assert rep["judge"]["criteria"][0]["calibrated"] is False
    assert rep["judge"]["pairwise"]["inconsistent_items"] == [comp["item_id"]]
    # Uncalibrated judge verdicts are excluded from the rubric score.
    items = client.get(f"/api/runs/{run_id}/items").json()
    outcome = items[0]["responses"][0]["verdict"]["rubric"]["outcomes"][0]
    assert outcome["source"] == "excluded"


def test_judge_refuses_own_family(client, fake_llm):
    fake_llm.responder = _judge_responder
    ids = _suite(client, "Judge2", [OPEN_ITEM])
    good = make_endpoint(client, "good", "good")              # qwen family
    bad = make_endpoint(client, "bad", "bad", model="llama3.2:3b")
    jdg = make_endpoint(client, "qwen judge", "", model="qwen3:4b")  # also qwen
    body = {"suite_version_id": ids["version_id"], "candidate_id": good["id"],
            "baseline_ids": [bad["id"]], "judge": {"enabled": True, "endpoint_id": jdg["id"]}}
    est = client.post("/api/runs/estimate", json=body).json()
    assert any("same model family" in w for w in est["warnings"])
    client.post("/api/runs", json=body)
    with db.session() as conn:
        ev = [json.loads(r["evidence"]) for r in conn.execute(
            "SELECT evidence FROM criterion_results WHERE source = 'judge'")]
    assert any("qwen family" in e.get("skipped", "") for e in ev)


def test_calibration_threshold():
    with db.session() as conn:
        conn.executescript(db.SCHEMA)
        conn.execute("INSERT INTO suites VALUES (1,'s','', 'now')")
        conn.execute("INSERT INTO suite_versions VALUES (1,1,1,1,'','now')")
        conn.execute("INSERT INTO items VALUES (1,1,'k','c','easy','p','{}',1,'',0)")
        conn.execute("INSERT INTO hidden_criteria VALUES (1,1,'crit','t','must',1,'judge','{}',NULL,0)")
        conn.execute("INSERT INTO runs (id,name,suite_version_id,status,config,created_at)"
                     " VALUES (1,'r',1,'completed','{}','now')")
        conn.execute("INSERT INTO run_models VALUES (1,1,'candidate',NULL,'m','{}',0)")
        for rid in range(1, 13):
            human = rid % 2 == 0
            judge_met = human if rid != 1 else (not human)  # 11/12 agreement
            conn.execute("INSERT INTO responses (id,run_id,run_model_id,item_id,repetition,status,"
                         "created_at) VALUES (?,1,1,1,?,'ok','now')", (rid, rid))
            conn.execute("INSERT INTO criterion_results (response_id,criterion_id,source,met,created_at)"
                         " VALUES (?,1,'human',?,'now')", (rid, int(human)))
            conn.execute("INSERT INTO criterion_results (response_id,criterion_id,source,met,created_at)"
                         " VALUES (?,1,'judge',?,'now')", (rid, int(judge_met)))
        cal = judge.calibration(conn)
    assert cal["crit"]["n"] == 12 and cal["crit"]["kappa"] > 0.6 and cal["crit"]["calibrated"]


def test_quote_matching_is_whitespace_and_case_tolerant():
    assert judge.quote_in("Unique   SALT", "use a unique salt")
    assert not judge.quote_in("a different phrase", "use a unique salt")
    assert not judge.quote_in("", "anything")


def test_cli_run_and_gate_exit_codes(client, fake_llm, capsys):
    def resp(model, system, prompt):
        return "Answer: 2" if system == "good" else "Answer: 3"
    fake_llm.responder = resp
    items = [{**ITEM, "key": f"q{i}", "prompt": f"Q{i}: 1+1? Answer: <n>"} for i in range(12)]
    ids = _suite(client, "CLI", items)
    make_endpoint(client, "v2", "bad")
    make_endpoint(client, "v1", "good")
    code = cli.main(["run", "--suite-version", str(ids["version_id"]), "--candidate", "v2",
                     "--baseline", "v1", "--gate", "--max-drop", "5"])
    out = capsys.readouterr().out
    assert code == 1 and "FAIL" in out and "local" in out
    with db.session() as conn:
        run_id = conn.execute("SELECT MAX(id) FROM runs").fetchone()[0]
    assert cli.main(["gate", "--run", str(run_id), "--max-drop", "100", "--allow-significant"]) == 0
    assert cli.main(["report", "--run", str(run_id), "--format", "md"]) == 0
    assert "Arbiter gap report" in capsys.readouterr().out
    providers.set_transport(None)


def test_migration_adds_columns_to_old_database():
    with db.session() as conn:
        conn.execute("CREATE TABLE responses (id INTEGER PRIMARY KEY, run_id INTEGER)")
        conn.execute("CREATE TABLE response_cache (key TEXT PRIMARY KEY, text TEXT)")
    db.init_db()
    with db.session() as conn:
        for table in ("responses", "response_cache"):
            assert "truncated" in {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
