"""Command line interface for jitterlens."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from .engine import run
from .errors import JitterLensError
from .fixturegen import generate_fixtures
from .ledger import query_ledger
from .report.jsonreport import canonical_json


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _query_output(db_path: str, table: str, limit: int) -> int:
    try:
        result = query_ledger(db_path, table, limit)
    except JitterLensError as exc:
        print(f"ERROR {exc}", file=sys.stderr)
        return 1
    print(canonical_json(result), end="")
    return 0


def _parse_query_alias(argv: list[str]) -> int | None:
    if "--query" not in argv:
        return None
    parser = argparse.ArgumentParser(prog="jitterlens --query", add_help=True)
    parser.add_argument("--query", required=True, choices=["runs", "sources", "intervals", "parse_errors", "plans"])
    parser.add_argument("--db", required=True)
    parser.add_argument("--limit", type=int, default=1000)
    args = parser.parse_args(argv)
    return _query_output(args.db, args.query, args.limit)


def _summary_line(report: dict[str, object]) -> str:
    summary = report["summary"]  # type: ignore[index]
    plans = summary["plans"]  # type: ignore[index]
    return (
        f"verdict={summary['verdict']} sources={summary['sources']} "
        f"points={summary['valid_points']} intervals={summary['abnormal_intervals']} "
        f"parse_errors={summary['parse_errors']} plans={plans['fill']}+{plans['downsample']} "
        f"truncated={plans['truncated']}"
    )


def _run_command(args: argparse.Namespace) -> int:
    artifact = run(
        contract_path=args.contract,
        input_dir=args.input_dir,
        out_dir=args.out_dir,
        generated_at=args.generated_at,
        with_html=args.html,
        ledger_path=args.db,
    )
    print(f"jitterlens: {_summary_line(artifact.report)}")
    print(f"outputs: {artifact.out_dir}")
    if args.no_fail_exit:
        return 0
    return artifact.exit_code


def _demo_command(args: argparse.Namespace) -> int:
    root = repo_root()
    fixtures = Path(args.fixtures_dir) if args.fixtures_dir else root / "fixtures" / "generated"
    contract = Path(args.contract) if args.contract else root / "contracts" / "sensors_default.yaml"
    out_dir = Path(args.out_dir) if args.out_dir else root / "build" / "demo"
    generated = generate_fixtures(fixtures)
    artifact = run(
        contract_path=contract,
        input_dir=fixtures,
        out_dir=out_dir,
        with_html=not args.no_html,
        ledger_path=None,
    )
    print(f"jitterlens demo: generated {len(generated)} fixtures")
    print(f"jitterlens demo: {_summary_line(artifact.report)}")
    print(f"reports: {artifact.out_dir}")
    if args.no_fail_exit:
        return 0
    return artifact.exit_code


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jitterlens", description="Offline sampling jitter verifier")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="analyze an input directory")
    run_parser.add_argument("--contract", required=True)
    run_parser.add_argument("--input-dir", required=True)
    run_parser.add_argument("--out-dir", required=True)
    run_parser.add_argument("--db", default=None)
    run_parser.add_argument("--generated-at", default=None)
    run_parser.add_argument("--html", action="store_true")
    run_parser.add_argument("--no-fail-exit", action="store_true")
    run_parser.set_defaults(handler=_run_command)

    demo_parser = subparsers.add_parser("demo", help="generate fixtures and run the default contract")
    demo_parser.add_argument("--contract", default=None)
    demo_parser.add_argument("--fixtures-dir", default=None)
    demo_parser.add_argument("--out-dir", default=None)
    demo_parser.add_argument("--no-html", action="store_true")
    demo_parser.add_argument("--no-fail-exit", action="store_true")
    demo_parser.set_defaults(handler=_demo_command)

    fixture_parser = subparsers.add_parser("gen-fixtures", help="write deterministic fixtures")
    fixture_parser.add_argument("--out-dir", default=None)
    fixture_parser.set_defaults(handler=_fixtures_command)

    query_parser = subparsers.add_parser("query", help="read-only ledger query")
    query_parser.add_argument("table", choices=["runs", "sources", "intervals", "parse_errors", "plans"])
    query_parser.add_argument("--db", required=True)
    query_parser.add_argument("--limit", type=int, default=1000)
    query_parser.set_defaults(handler=_query_command)
    return parser


def _fixtures_command(args: argparse.Namespace) -> int:
    target = Path(args.out_dir) if args.out_dir else repo_root() / "fixtures" / "generated"
    paths = generate_fixtures(target)
    print(f"generated {len(paths)} fixtures in {target}")
    return 0


def _query_command(args: argparse.Namespace) -> int:
    return _query_output(args.db, args.table, args.limit)


def main(argv: Sequence[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    alias = _parse_query_alias(raw)
    if alias is not None:
        return alias
    parser = build_parser()
    args = parser.parse_args(raw)
    try:
        return int(args.handler(args))
    except JitterLensError as exc:
        print(f"ERROR {exc}", file=sys.stderr)
        return 1
    except (OSError, ValueError) as exc:
        print(f"ERROR {exc}", file=sys.stderr)
        return 1
