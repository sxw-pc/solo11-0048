"""Self-contained HTML report using inline CSS only."""

from __future__ import annotations

from html import escape
from pathlib import Path
from typing import Any

_STYLE = """
body { font-family: system-ui, sans-serif; margin: 2rem; color: #1d2733; background: #fafbfc; }
h1, h2, h3 { color: #16324f; }
table { border-collapse: collapse; margin: 0.8rem 0 1.5rem; width: 100%; }
th, td { border: 1px solid #c9d4df; padding: 0.35rem 0.55rem; text-align: left; }
th { background: #e8eef4; }
.ok { color: #17643a; font-weight: 700; }
.warn { color: #8a5a00; font-weight: 700; }
.fail { color: #a11b1b; font-weight: 700; }
code { background: #eef2f5; padding: 0.05rem 0.25rem; }
.note { color: #52606d; }
"""


def _e(value: Any) -> str:
    return escape(str(value), quote=True)


def _table(headers: list[str], rows: list[list[Any]]) -> str:
    head = "".join(f"<th>{_e(item)}</th>" for item in headers)
    body = "".join(
        "<tr>" + "".join(f"<td>{_e(item)}</td>" for item in row) + "</tr>" for row in rows
    )
    if not rows:
        body = "<tr><td colspan=\"" + str(len(headers)) + "\"><em>none</em></td></tr>"
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def render_html(report: dict[str, Any]) -> str:
    summary = report["summary"]
    contract = report["contract"]
    verdict = str(summary["verdict"])
    pieces = [
        "<!DOCTYPE html>",
        '<html lang="en"><head><meta charset="utf-8">',
        "<title>Sampling Jitter Report</title>",
        f"<style>{_STYLE}</style></head><body>",
        "<h1>Sampling Jitter Report</h1>",
        f'<p>Verdict: <span class="{_e(verdict)}">{_e(verdict)}</span></p>',
        "<p>"
        f"Run <code>{_e(report['run_id'])}</code>; generated <code>{_e(report['generated_at'])}</code>; "
        f"contract <code>{_e(contract['name'])}</code> (sha256 <code>{_e(contract['sha256'][:16])}...</code>)."
        "</p>",
        "<h2>Summary</h2>",
        _table(
            ["Metric", "Value"],
            [
                ["Sources", summary["sources"]],
                ["Valid points", summary["valid_points"]],
                ["Parse errors", summary["parse_errors"]],
                ["Abnormal intervals", summary["abnormal_intervals"]],
                ["Fill plans", summary["plans"]["fill"]],
                ["Downsample plans", summary["plans"]["downsample"]],
                ["Truncated plans", summary["plans"]["truncated"]],
            ],
        ),
        "<h2>Sources</h2>",
    ]
    rows = []
    for source in report["sources"]:
        counts = source["counts"]
        rows.append(
            [
                source["name"],
                source["verdict"],
                counts["valid_points"],
                counts["parse_errors"],
                counts["jitter"],
                counts["gap"],
                counts["clock_drift"],
                source["gap_ratio"],
                source["jitter_ratio"],
            ]
        )
    pieces.append(
        _table(
            ["Source", "Verdict", "Points", "Parse errors", "Jitter", "Gap", "Drift", "Gap ratio", "Jitter ratio"],
            rows,
        )
    )
    pieces.append("<h2>Abnormal intervals</h2>")
    interval_rows = [
        [
            item["id"],
            item["source"],
            item["start_ts"],
            item["end_ts"],
            ",".join(item["kinds"]),
            item["point_count"],
            f"{item['start_origin']}:{item['start_lineno']}",
        ]
        for item in report["intervals"][:50]
    ]
    pieces.append(
        _table(["ID", "Source", "Start ts", "End ts", "Kinds", "Points", "Location"], interval_rows)
    )
    pieces.append("<h2>Parse errors</h2>")
    parse_rows = [
        [item["source"], f"{item['origin']}:{item['lineno']}", item["code"], item["message"]]
        for item in report["parse_errors"][:50]
    ]
    pieces.append(_table(["Source", "Location", "Code", "Message"], parse_rows))
    pieces.append(
        '<p class="note">This report is self-contained and makes no network requests. '
        "Plans are advisory; input files are never modified.</p>"
    )
    pieces.append("</body></html>")
    return "\n".join(pieces) + "\n"


def write_html(path: str | Path, report: dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(render_html(report))
