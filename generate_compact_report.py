#!/usr/bin/env python3
"""Generate a compact wallet x agent interop matrix from a pytest run.

Reads pytest-html report.html files inside a given run directory and emits
a self-contained, embeddable HTML report (plus a JSON twin) under status/.

Usage:
    python generate_compact_report.py reports/2026-05-04_10-13-21
    python generate_compact_report.py reports/<run> --embed-icons
    python generate_compact_report.py reports/<run> --output some/dir
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import html
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parent
DEFAULT_OUTPUT = REPO_ROOT / "status"

OUTCOME_RANK = {None: 0, "Passed": 1, "Skipped": 2, "Error": 3, "Failed": 4}
OUTCOME_CLASS = {"Passed": "ok", "Failed": "fail", "Error": "err", "Skipped": "skip"}

# Inline SVG status glyphs — render identically across platforms (no emoji-font dependency).
SVG_PASS = (
    '<svg class="g g-pass" viewBox="0 0 20 20" aria-label="Passed">'
    '<circle cx="10" cy="10" r="9" fill="#1aa861"/>'
    '<path d="M5.6 10.2l2.9 2.9 6-6.3" fill="none" stroke="#fff" stroke-width="2.2" '
    'stroke-linecap="round" stroke-linejoin="round"/></svg>'
)
SVG_FAIL = (
    '<svg class="g g-fail" viewBox="0 0 20 20" aria-label="Failed">'
    '<circle cx="10" cy="10" r="9" fill="#d4452c"/>'
    '<path d="M6.5 6.5l7 7M13.5 6.5l-7 7" fill="none" stroke="#fff" stroke-width="2.2" '
    'stroke-linecap="round"/></svg>'
)
SVG_ERR = (
    '<svg class="g g-err" viewBox="0 0 20 20" aria-label="Errored">'
    '<circle cx="10" cy="10" r="9" fill="#d68a17"/>'
    '<path d="M10 5.5v5.2M10 13.6v.6" fill="none" stroke="#fff" stroke-width="2.2" '
    'stroke-linecap="round"/></svg>'
)
SVG_SKIP = (
    '<svg class="g g-skip" viewBox="0 0 20 20" aria-label="Skipped">'
    '<circle cx="10" cy="10" r="9" fill="#9aa0a6"/>'
    '<path d="M6 10h8" fill="none" stroke="#fff" stroke-width="2.2" stroke-linecap="round"/></svg>'
)
SVG_NONE = (
    '<svg class="g g-none" viewBox="0 0 20 20" aria-label="No test">'
    '<circle cx="10" cy="10" r="2.4" fill="#cfd2d6"/></svg>'
)
OUTCOME_SVG = {"Passed": SVG_PASS, "Failed": SVG_FAIL, "Error": SVG_ERR, "Skipped": SVG_SKIP}
OUTCOME_GLYPH = {"Passed": "✓", "Failed": "✕", "Error": "!", "Skipped": "–"}

JSONBLOB_RE = re.compile(r'data-jsonblob="([^"]+)"')
TESTID_RE = re.compile(
    r"wallets/(?P<wallet>[^/]+)/tests/test_credential_(?P<flow>issuance|verification)\.py"
    r"::test_credential_(?:issuance|verification)\[(?P<agent>[^/]+)/(?P<case>.+)-(?P=wallet)\]"
)


def resolve_run_dir(arg: str) -> Path:
    p = Path(arg)
    if not p.is_absolute():
        p = (Path.cwd() / p).resolve()
    if not p.is_dir():
        sys.exit(f"Run dir not found: {p}")
    return p


def load_pytest_html(report_html: Path) -> dict:
    text = report_html.read_text(encoding="utf-8", errors="replace")
    match = JSONBLOB_RE.search(text)
    if not match:
        return {"tests": {}}
    return json.loads(html.unescape(match.group(1)))


def parse_test_id(test_id: str):
    match = TESTID_RE.search(test_id)
    if not match:
        return None
    return match["wallet"], match["flow"], match["agent"], match["case"]


def collect(run_dir: Path) -> dict:
    wallets: list[str] = []
    issuance: dict[str, dict[str, str]] = {}
    verification: dict[str, dict[str, str]] = {}
    issuance_agents: set[str] = set()
    verification_agents: set[str] = set()
    totals = {"Passed": 0, "Failed": 0, "Error": 0, "Skipped": 0}

    for child in sorted(run_dir.iterdir()):
        if not child.is_dir():
            continue
        report = child / "report.html"
        if not report.exists():
            continue
        wallet = child.name
        wallets.append(wallet)
        data = load_pytest_html(report)
        for tid, recs in data.get("tests", {}).items():
            rec = recs[-1] if isinstance(recs, list) and recs else (recs if isinstance(recs, dict) else {})
            outcome = rec.get("result") if isinstance(rec, dict) else None
            parsed = parse_test_id(tid)
            if not parsed:
                continue
            _, flow, agent, _case = parsed
            if flow == "issuance":
                target, agents_set = issuance, issuance_agents
            else:
                target, agents_set = verification, verification_agents
            agents_set.add(agent)
            cell = target.setdefault(wallet, {})
            existing = cell.get(agent)
            if OUTCOME_RANK.get(outcome, 0) >= OUTCOME_RANK.get(existing, 0):
                cell[agent] = outcome
            if outcome in totals:
                totals[outcome] += 1

    return {
        "run_ts": run_dir.name,
        "wallets": wallets,
        "issuance_agents": sorted(issuance_agents),
        "verification_agents": sorted(verification_agents),
        "issuance": issuance,
        "verification": verification,
        "totals": totals,
    }


def hash_color(name: str) -> str:
    h = int(hashlib.md5(name.encode()).hexdigest(), 16)
    return f"hsl({h % 360}, 55%, 50%)"


def initials(name: str) -> str:
    parts = [p for p in name.split("_") if p]
    if len(parts) >= 2:
        return (parts[0][0] + parts[1][0]).upper()
    return name[:2].upper()


def find_icon(name: str, role: str, output_dir: Path) -> Optional[Path]:
    base_dir = output_dir / "icons" / role
    candidates = [base_dir / f"{name}.png"]
    stripped = re.sub(r"_(pension|verifier|issuer)$", "", name)
    if stripped != name:
        candidates.append(base_dir / f"{stripped}.png")
    for c in candidates:
        if c.exists():
            return c
    return None


def icon_html(name: str, role: str, output_dir: Path, embed: bool) -> str:
    path = find_icon(name, role, output_dir)
    if path is not None:
        if embed:
            b64 = base64.b64encode(path.read_bytes()).decode("ascii")
            src = f"data:image/png;base64,{b64}"
        else:
            src = f"icons/{role}/{path.name}"
        return f'<img class="icon" src="{html.escape(src, quote=True)}" alt="" />'
    return (
        f'<span class="icon badge" style="background:{hash_color(name)}">'
        f'{html.escape(initials(name))}</span>'
    )


CSS = """
:root {
  --bg: #f4f5f7;
  --card: #ffffff;
  --ink: #1a1d23;
  --muted: #6b7280;
  --line: #e6e8eb;
  --line-strong: #d3d6da;
  --ok: #1aa861;
  --fail: #d4452c;
  --err: #d68a17;
  --skip: #9aa0a6;
  --row-hover: #f8f9fb;
}
* { box-sizing: border-box; }
html, body { margin: 0; padding: 0; }
body {
  font-family: "Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
  background: var(--bg);
  color: var(--ink);
  padding: 28px 16px;
  -webkit-font-smoothing: antialiased;
  font-feature-settings: "cv11", "ss01";
}
.container {
  max-width: 1100px;
  margin: 0 auto;
  background: var(--card);
  border-radius: 14px;
  box-shadow: 0 1px 2px rgba(20,24,32,.04), 0 8px 24px rgba(20,24,32,.06);
  overflow: hidden;
}
.header {
  padding: 22px 28px 18px;
  background: linear-gradient(180deg, #fbfcfd 0%, #ffffff 100%);
  border-bottom: 1px solid var(--line);
  display: flex;
  align-items: center;
  gap: 14px;
}
.header .logo { width: 34px; height: 34px; object-fit: contain; flex: none; }
.header .titles { flex: 1; min-width: 0; }
h1 { margin: 0; font-size: 1.15rem; font-weight: 700; letter-spacing: -.01em; }
.run-ts {
  display: inline-block;
  margin-top: 4px;
  font-size: .78rem;
  color: var(--muted);
  font-variant-numeric: tabular-nums;
  letter-spacing: .01em;
}
.totals {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  flex: none;
}
.pill {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 10px;
  border-radius: 999px;
  font-size: .78rem;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  background: #eef0f3;
  color: var(--muted);
}
.pill.ok   { background: rgba(26,168,97,.12); color: var(--ok); }
.pill.fail { background: rgba(212,69,44,.12); color: var(--fail); }
.pill.err  { background: rgba(214,138,23,.14); color: var(--err); }
.pill.skip { background: #eef0f3; color: var(--skip); }
.pill svg { width: 12px; height: 12px; }

.table-wrap { padding: 6px 0 0; overflow-x: auto; }
table {
  width: 100%;
  border-collapse: separate;
  border-spacing: 0;
  font-size: .92rem;
  font-variant-numeric: tabular-nums;
}
th, td {
  padding: 12px 14px;
  text-align: center;
  vertical-align: middle;
  border-bottom: 1px solid var(--line);
}
th.row-head, td.row-head {
  text-align: left;
  font-weight: 500;
  padding-left: 28px;
  min-width: 160px;
}
th {
  background: #fafbfc;
  font-weight: 600;
  color: #374151;
  font-size: .82rem;
  letter-spacing: .01em;
}
tr.section th.group {
  text-align: left;
  text-transform: uppercase;
  letter-spacing: .08em;
  font-size: .7rem;
  color: var(--muted);
  background: #f4f5f7;
  border-top: 1px solid var(--line-strong);
  border-bottom: 1px solid var(--line-strong);
  padding: 8px 28px;
  font-weight: 700;
}
tr.section:first-child th.group { border-top: none; }
tbody tr:last-child td { border-bottom: none; }
tbody tr:not(.section):hover td { background: var(--row-hover); }
tbody tr td:first-child { padding-left: 28px; }
tbody tr td:last-child  { padding-right: 28px; }
th:last-child { padding-right: 28px; }

.label { display: inline-flex; align-items: center; gap: 10px; }
.label > span { font-weight: 500; color: var(--ink); }
th .label > span { font-weight: 600; color: #374151; font-size: .82rem; }
.icon {
  display: inline-block;
  width: 24px; height: 24px;
  border-radius: 6px;
  object-fit: contain;
  background: #f0f1f4;
  flex: none;
}
.icon.badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  color: #fff;
  font-size: .7rem;
  font-weight: 700;
  line-height: 1;
  background-clip: padding-box;
}
.cell { line-height: 0; }
.g {
  width: 22px; height: 22px;
  display: inline-block;
  vertical-align: middle;
}
.g-none { opacity: .55; }

.footer {
  padding: 14px 28px 20px;
  font-size: .74rem;
  color: var(--muted);
  text-align: right;
  border-top: 1px solid var(--line);
}
.empty { color: var(--muted); font-size: .9rem; padding: 28px; text-align: center; }

@media (max-width: 640px) {
  body { padding: 12px 6px; }
  .header { flex-wrap: wrap; padding: 16px 18px; }
  th.row-head, td.row-head { padding-left: 18px; min-width: 130px; }
  tbody tr td:first-child { padding-left: 18px; }
  tbody tr td:last-child  { padding-right: 18px; }
  th:last-child { padding-right: 18px; }
}
"""

PAGE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>Wallet Interop Status - {run_ts}</title>
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
  <style>{css}</style>
</head>
<body>
  <div class="container">
    <div class="header">
      {logo}
      <div class="titles">
        <h1>Wallet Interop Status</h1>
        <div class="run-ts">Run {run_ts}</div>
      </div>
      <div class="totals">{pills}</div>
    </div>
    <div class="table-wrap">{sections}</div>
    <div class="footer">Generated {generated}</div>
  </div>
</body>
</html>
"""


def _outcome_td(outcome: Optional[str]) -> str:
    if outcome:
        cls = OUTCOME_CLASS.get(outcome, "none")
        glyph = OUTCOME_SVG.get(outcome, SVG_NONE)
        title_attr = outcome
    else:
        cls, glyph, title_attr = "none", SVG_NONE, "no test"
    return f'<td class="cell {cls}" title="{html.escape(title_attr, quote=True)}">{glyph}</td>'


def _agent_row(agent: str, wallets: list, data: dict, output_dir: Path, embed: bool) -> str:
    head = (
        "<td class='row-head'><div class='label'>"
        + icon_html(agent, "agents", output_dir, embed)
        + f"<span>{html.escape(agent)}</span></div></td>"
    )
    cells = "".join(_outcome_td(data.get(w, {}).get(agent)) for w in wallets)
    return f"<tr>{head}{cells}</tr>"


def render_combined_table(matrix: dict, output_dir: Path, embed: bool) -> str:
    issuance_agents = matrix["issuance_agents"]
    verification_agents = matrix["verification_agents"]
    wallets = matrix["wallets"]
    issuance = matrix["issuance"]
    verification = matrix["verification"]

    if not issuance_agents and not verification_agents:
        return "<p class='empty'>No issuance or verification tests found in this run.</p>"

    if not wallets:
        return "<p class='empty'>No wallets found in this run.</p>"

    # Column headers: blank corner + one column per wallet
    head_cells = ["<th class='row-head'></th>"]
    for w in wallets:
        head_cells.append(
            "<th><div class='label'>"
            + icon_html(w, "wallets", output_dir, embed)
            + f"<span>{html.escape(w)}</span></div></th>"
        )
    header_row = "<tr>" + "".join(head_cells) + "</tr>"

    # Body: section dividers + one row per agent
    span = len(wallets) + 1
    body_parts = []
    if issuance_agents:
        body_parts.append(
            f"<tr class='section'><th class='group' colspan='{span}'>Issuance</th></tr>"
        )
        for a in issuance_agents:
            body_parts.append(_agent_row(a, wallets, issuance, output_dir, embed))
    if verification_agents:
        body_parts.append(
            f"<tr class='section'><th class='group' colspan='{span}'>Verification</th></tr>"
        )
        for a in verification_agents:
            body_parts.append(_agent_row(a, wallets, verification, output_dir, embed))

    return (
        "<table>"
        f"<thead>{header_row}</thead>"
        f"<tbody>{''.join(body_parts)}</tbody>"
        "</table>"
    )


def _logo_html(output_dir: Path, embed: bool) -> str:
    logo = output_dir / "icons" / "findynet.png"
    if not logo.exists():
        return ""
    if embed:
        b64 = base64.b64encode(logo.read_bytes()).decode("ascii")
        src = f"data:image/png;base64,{b64}"
    else:
        src = "icons/findynet.png"
    return f'<img class="logo" src="{html.escape(src, quote=True)}" alt="" />'


def _pills_html(totals: dict) -> str:
    parts = [
        ("ok",   SVG_PASS, totals.get("Passed", 0),  "passing"),
        ("fail", SVG_FAIL, totals.get("Failed", 0),  "failing"),
    ]
    if totals.get("Error"):
        parts.append(("err",  SVG_ERR,  totals["Error"],   "errored"))
    if totals.get("Skipped"):
        parts.append(("skip", SVG_SKIP, totals["Skipped"], "skipped"))
    return "".join(
        f'<span class="pill {cls}">{svg}<span>{n} {label}</span></span>'
        for cls, svg, n, label in parts
    )


def render_html(matrix: dict, output_dir: Path, embed: bool) -> str:
    sections = render_combined_table(matrix, output_dir, embed)
    return PAGE.format(
        run_ts=html.escape(matrix["run_ts"]),
        css=CSS,
        logo=_logo_html(output_dir, embed),
        pills=_pills_html(matrix["totals"]),
        sections=sections,
        generated=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("run_dir", help="Path to a reports/<timestamp> directory")
    ap.add_argument("--output", default=str(DEFAULT_OUTPUT),
                    help="Output directory (default: <repo>/status/)")
    ap.add_argument("--embed-icons", action="store_true",
                    help="Inline icons as base64 data URIs for a single-file HTML")
    args = ap.parse_args(argv)

    run_dir = resolve_run_dir(args.run_dir)
    output_dir = Path(args.output)
    if not output_dir.is_absolute():
        output_dir = (Path.cwd() / output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    matrix = collect(run_dir)
    page = render_html(matrix, output_dir, args.embed_icons)
    (output_dir / "index.html").write_text(page, encoding="utf-8")
    (output_dir / "data.json").write_text(json.dumps(matrix, indent=2), encoding="utf-8")
    print(f"wrote {output_dir / 'index.html'}")
    print(f"wrote {output_dir / 'data.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
