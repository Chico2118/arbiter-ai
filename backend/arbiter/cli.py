"""Command-line interface for CI pipelines.

    python -m arbiter.cli list
    python -m arbiter.cli run --suite-version 1 --candidate "qwen3 v2" --baseline "qwen3 v1" \
        --limit 10 --gate --max-drop 5
    python -m arbiter.cli report --run 3 --format md --out report.md
    python -m arbiter.cli gate --run 3 --max-drop 5 --no-unsafe

``gate`` (and ``run --gate``) exit with status 1 when the release gate fails,
so a CI job fails on a regression.
"""

from __future__ import annotations

import argparse
import json
import sys

from . import db, report, runner, suites
from .schemas import RunIn


def _endpoint_id(conn, ref: str) -> int:
    row = conn.execute("SELECT id FROM endpoints WHERE name = ? OR CAST(id AS TEXT) = ?",
                       (ref, ref)).fetchone()
    if row is None:
        raise SystemExit(f"error: endpoint '{ref}' not found (use the name or id)")
    return int(row["id"])


def _print_gate(result: dict) -> None:
    print(f"Release gate vs {result['baseline']}: {'PASS' if result['passed'] else 'FAIL'}")
    for r in result["rules"]:
        print(f"  [{'pass' if r['passed'] else 'FAIL'}] {r['rule']}: {r['detail']}")


def _gate_kwargs(args) -> dict:
    return {"max_category_drop": args.max_drop, "min_overall": args.min_overall,
            "no_significant_regression": not args.allow_significant,
            "max_unsafe": 0 if args.no_unsafe else None}


def cmd_list(_args) -> int:
    with db.session() as conn:
        print("Endpoints:")
        for e in conn.execute("SELECT id, name, provider, model FROM endpoints ORDER BY id"):
            print(f"  #{e['id']:<3} {e['name']:<28} {e['provider']:<18} {e['model']}")
        print("Suite versions:")
        for v in conn.execute("SELECT v.id, v.version, v.locked, s.name FROM suite_versions v"
                              " JOIN suites s ON s.id = v.suite_id ORDER BY v.id"):
            n = conn.execute("SELECT COUNT(*) FROM items WHERE suite_version_id = ?",
                             (v["id"],)).fetchone()[0]
            print(f"  #{v['id']:<3} {v['name']} v{v['version']} ({n} items"
                  f"{', locked' if v['locked'] else ''})")
        print("Runs:")
        for r in conn.execute("SELECT id, name, status FROM runs ORDER BY id DESC LIMIT 20"):
            print(f"  #{r['id']:<3} {r['status']:<16} {r['name']}")
    return 0


def cmd_run(args) -> int:
    with db.session() as conn:
        items = suites.get_items(conn, args.suite_version)
        if args.category:
            items = [i for i in items if i["category"] in args.category]
        if args.limit:
            items = items[: args.limit]
        spec = RunIn(name=args.name or "", suite_version_id=args.suite_version,
                     candidate_id=_endpoint_id(conn, args.candidate),
                     baseline_ids=[_endpoint_id(conn, b) for b in args.baseline],
                     repetitions=args.reps, item_ids=[i["id"] for i in items],
                     budget_usd=args.budget)
        est = runner.estimate(conn, spec)
        for e in est["endpoints"]:
            where = "local" if e["local"] else f"{e['company']} ({e['host']})"
            print(f"  {e['role']:<9} {e['name']:<24} -> {where}, {e['calls']} calls,"
                  f" est. ${e['est_cost_typical']:.4f} (max ${e['est_cost_max']:.4f})")
        run_id = runner.create_run(conn, spec)
    print(f"Run #{run_id} started ({est['items']} items x {args.reps} reps)...")
    runner.execute_run(run_id)
    with db.session() as conn:
        row = conn.execute("SELECT status, error FROM runs WHERE id = ?", (run_id,)).fetchone()
        rep = report.build_report(conn, run_id)
    print(f"Run #{run_id} finished: {row['status']}" + (f" ({row['error']})" if row["error"] else ""))
    for line in rep["summary"]["bullets"]:
        print(f"- {line}")
    if args.gate:
        result = report.gate(rep, **_gate_kwargs(args))
        _print_gate(result)
        return 0 if result["passed"] else 1
    return 0 if row["status"] == "completed" else 2


def cmd_report(args) -> int:
    with db.session() as conn:
        rep = report.build_report(conn, args.run)
    if rep is None:
        raise SystemExit(f"error: run {args.run} not found")
    text = report.to_markdown(rep) if args.format == "md" else json.dumps(rep, indent=2, default=str)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text)
        print(f"Wrote {args.out}")
    else:
        sys.stdout.write(text + "\n")
    return 0


def cmd_gate(args) -> int:
    with db.session() as conn:
        rep = report.build_report(conn, args.run)
    if rep is None:
        raise SystemExit(f"error: run {args.run} not found")
    result = report.gate(rep, **_gate_kwargs(args))
    _print_gate(result)
    return 0 if result["passed"] else 1


def _add_gate_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--max-drop", type=float, default=5.0,
                   help="max allowed per-category drop in points (default 5)")
    p.add_argument("--min-overall", type=float, default=None, help="min overall accuracy (%%)")
    p.add_argument("--allow-significant", action="store_true",
                   help="do not fail on a significant McNemar regression")
    p.add_argument("--no-unsafe", action="store_true", help="fail if any response is unsafe")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="arbiter", description="Arbiter CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="list endpoints, suite versions and runs")
    p = sub.add_parser("run", help="run a comparison synchronously")
    p.add_argument("--suite-version", type=int, required=True)
    p.add_argument("--candidate", required=True)
    p.add_argument("--baseline", action="append", required=True)
    p.add_argument("--reps", type=int, default=1, choices=[1, 2, 3])
    p.add_argument("--limit", type=int, default=None, help="only the first N items")
    p.add_argument("--category", action="append", help="restrict to category (repeatable)")
    p.add_argument("--budget", type=float, default=1.0)
    p.add_argument("--name", default="")
    p.add_argument("--gate", action="store_true", help="apply the release gate afterwards")
    _add_gate_args(p)
    p = sub.add_parser("report", help="export a run report")
    p.add_argument("--run", type=int, required=True)
    p.add_argument("--format", choices=["md", "json"], default="md")
    p.add_argument("--out")
    p = sub.add_parser("gate", help="evaluate the release gate for a run")
    p.add_argument("--run", type=int, required=True)
    _add_gate_args(p)
    args = parser.parse_args(argv)
    db.init_db()
    with db.session() as conn:
        suites.load_seed(conn)
    return {"list": cmd_list, "run": cmd_run, "report": cmd_report, "gate": cmd_gate}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
