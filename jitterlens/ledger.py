"""Append-only SQLite run ledger and read-only queries."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .errors import JitterLensError

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  pk INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL,
  generated_at TEXT NOT NULL,
  contract_name TEXT NOT NULL,
  contract_sha256 TEXT NOT NULL,
  input_sha256 TEXT NOT NULL,
  verdict TEXT NOT NULL,
  sources INTEGER NOT NULL,
  valid_points INTEGER NOT NULL,
  parse_errors INTEGER NOT NULL,
  intervals INTEGER NOT NULL,
  summary_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sources (
  pk INTEGER PRIMARY KEY AUTOINCREMENT,
  run_pk INTEGER NOT NULL,
  name TEXT NOT NULL,
  verdict TEXT NOT NULL,
  points INTEGER NOT NULL,
  parse_errors INTEGER NOT NULL,
  gap_ratio REAL NOT NULL,
  jitter_ratio REAL NOT NULL,
  interval_count INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS intervals (
  pk INTEGER PRIMARY KEY AUTOINCREMENT,
  run_pk INTEGER NOT NULL,
  interval_id TEXT NOT NULL,
  source TEXT NOT NULL,
  start_ts INTEGER NOT NULL,
  end_ts INTEGER NOT NULL,
  kinds TEXT NOT NULL,
  point_count INTEGER NOT NULL,
  start_origin TEXT NOT NULL,
  start_lineno INTEGER NOT NULL,
  end_origin TEXT NOT NULL,
  end_lineno INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS parse_errors (
  pk INTEGER PRIMARY KEY AUTOINCREMENT,
  run_pk INTEGER NOT NULL,
  source TEXT NOT NULL,
  origin TEXT NOT NULL,
  lineno INTEGER NOT NULL,
  code TEXT NOT NULL,
  message TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS plans (
  pk INTEGER PRIMARY KEY AUTOINCREMENT,
  run_pk INTEGER NOT NULL,
  plan_id TEXT NOT NULL,
  source TEXT NOT NULL,
  kind TEXT NOT NULL,
  start_ts INTEGER NOT NULL,
  end_ts INTEGER NOT NULL,
  expected_missing INTEGER,
  action_count INTEGER NOT NULL,
  truncated INTEGER NOT NULL,
  path TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sources_run ON sources(run_pk);
CREATE INDEX IF NOT EXISTS idx_intervals_run ON intervals(run_pk);
CREATE INDEX IF NOT EXISTS idx_parse_run ON parse_errors(run_pk);
CREATE INDEX IF NOT EXISTS idx_plans_run ON plans(run_pk);
"""

_QUERIES = {
    "runs": (
        "SELECT pk, run_id, generated_at, contract_name, contract_sha256, input_sha256, "
        "verdict, sources, valid_points, parse_errors, intervals FROM runs ORDER BY pk"
    ),
    "sources": (
        "SELECT pk, run_pk, name, verdict, points, parse_errors, gap_ratio, jitter_ratio, "
        "interval_count FROM sources ORDER BY pk"
    ),
    "intervals": (
        "SELECT pk, run_pk, interval_id, source, start_ts, end_ts, kinds, point_count, "
        "start_origin, start_lineno, end_origin, end_lineno FROM intervals ORDER BY pk"
    ),
    "parse_errors": (
        "SELECT pk, run_pk, source, origin, lineno, code, message FROM parse_errors ORDER BY pk"
    ),
    "plans": (
        "SELECT pk, run_pk, plan_id, source, kind, start_ts, end_ts, expected_missing, "
        "action_count, truncated, path FROM plans ORDER BY pk"
    ),
}


def record_run(
    db_path: str | Path,
    report: dict[str, Any],
    plans_document: dict[str, Any],
    plans_file: str,
) -> int:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    summary = report["summary"]
    connection = sqlite3.connect(path)
    try:
        connection.executescript(_SCHEMA)
        cursor = connection.execute(
            "INSERT INTO runs (run_id, generated_at, contract_name, contract_sha256, "
            "input_sha256, verdict, sources, valid_points, parse_errors, intervals, summary_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                report["run_id"],
                report["generated_at"],
                report["contract"]["name"],
                report["contract"]["sha256"],
                report["inputs"]["manifest_sha256"],
                summary["verdict"],
                summary["sources"],
                summary["valid_points"],
                summary["parse_errors"],
                summary["abnormal_intervals"],
                json.dumps(summary, ensure_ascii=False, sort_keys=True),
            ),
        )
        run_pk = int(cursor.lastrowid or 0)
        for source in report["sources"]:
            counts = source["counts"]
            connection.execute(
                "INSERT INTO sources (run_pk, name, verdict, points, parse_errors, gap_ratio, "
                "jitter_ratio, interval_count) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    run_pk,
                    source["name"],
                    source["verdict"],
                    counts["valid_points"],
                    counts["parse_errors"],
                    source["gap_ratio"],
                    source["jitter_ratio"],
                    counts["intervals"],
                ),
            )
        for interval in report["intervals"]:
            connection.execute(
                "INSERT INTO intervals (run_pk, interval_id, source, start_ts, end_ts, kinds, "
                "point_count, start_origin, start_lineno, end_origin, end_lineno) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    run_pk,
                    interval["id"],
                    interval["source"],
                    interval["start_ts"],
                    interval["end_ts"],
                    ",".join(interval["kinds"]),
                    interval["point_count"],
                    interval["start_origin"],
                    interval["start_lineno"],
                    interval["end_origin"],
                    interval["end_lineno"],
                ),
            )
        for issue in report["parse_errors"]:
            connection.execute(
                "INSERT INTO parse_errors (run_pk, source, origin, lineno, code, message) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    run_pk,
                    issue["source"],
                    issue["origin"],
                    issue["lineno"],
                    issue["code"],
                    issue["message"],
                ),
            )
        for plan in plans_document["plans"]:
            action_count = len(plan.get("insert_ts", plan.get("keep_ts", [])))
            connection.execute(
                "INSERT INTO plans (run_pk, plan_id, source, kind, start_ts, end_ts, "
                "expected_missing, action_count, truncated, path) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    run_pk,
                    plan["id"],
                    plan["source"],
                    plan["kind"],
                    plan["start_ts"],
                    plan["end_ts"],
                    plan.get("expected_missing"),
                    action_count,
                    1 if plan["truncated"] else 0,
                    plans_file,
                ),
            )
        connection.commit()
        return run_pk
    finally:
        connection.close()


def query_ledger(db_path: str | Path, table: str, limit: int = 1000) -> dict[str, Any]:
    if table not in _QUERIES:
        raise JitterLensError("E_LEDGER_002", f"unknown ledger table: {table}")
    path = Path(db_path)
    if not path.is_file():
        raise JitterLensError("E_LEDGER_001", f"ledger not found: {path}")
    query = _QUERIES[table] + " LIMIT ?"
    try:
        connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    except sqlite3.Error as exc:
        raise JitterLensError("E_LEDGER_001", f"cannot open ledger: {exc}") from exc
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        rows = [dict(row) for row in connection.execute(query, (limit,))]
    except sqlite3.Error as exc:
        raise JitterLensError("E_LEDGER_001", f"ledger query failed: {exc}") from exc
    finally:
        connection.close()
    return {"table": table, "count": len(rows), "rows": rows}
