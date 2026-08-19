#!/usr/bin/env python3
"""Generate a compact wallet x agent interop matrix from a pytest run.

Reads pytest-html report.html files inside a given run directory and emits
a self-contained, embeddable HTML report (plus a JSON twin) under status/.

Rows are agents (issuers / verifiers), columns are wallets. A wallet that adds
its own parametrize dimension gets one column per value, so gataca's DID
methods appear as "Gataca (jwk)" and "Gataca (ebsi)" rather than collapsing
into a single cell. Only the issuance and verification suites are charted;
install / onboarding / cleanup results stay in the per-wallet pytest reports.

The explanatory blocks under the matrix (about / status key / how to read it)
are content, not code: they live in .report_info.json inside the output
directory, alongside index.html and icons/. That file is gitignored so the
wording stays out of versioning; report_info.example.json beside it documents
the shape. Missing file means the report renders without the info blocks.

Usage:
    python generate_compact_report.py reports/2026-05-04_10-13-21
    python generate_compact_report.py reports/<run> --embed-icons
    python generate_compact_report.py reports/<run> --output some/dir
    python generate_compact_report.py reports/<run> --info other/info.json
    python generate_compact_report.py reports/<run> --no-info
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
# Explanatory-block content, read from the output directory. It is site content
# like icons/ and index.html, so it lives with the published site rather than
# here. The live copy is dot-prefixed and gitignored so the wording is not
# versioned; the template beside it is, so the shape stays documented.
INFO_FILENAME = ".report_info.json"
INFO_TEMPLATE = "report_info.example.json"

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

# Glyphs a report_info.json link may reference by name via its "icon" key.
# The path data lives here so the JSON file stays readable prose.
LINK_ICONS = {
    "github": (
        '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 .297c-6.63 0-12 5.373-12 12 0 5.303 3.438 9.8 8.205 11.385.6.113.82-.258.82-.577 0-.285-.01-1.04-.015-2.04-3.338.724-4.042-1.61-4.042-1.61C4.422 18.07 3.633 17.7 3.633 17.7c-1.087-.744.084-.729.084-.729 1.205.084 1.838 1.236 1.838 1.236 1.07 1.835 2.809 1.305 3.495.998.108-.776.417-1.305.76-1.605-2.665-.3-5.466-1.332-5.466-5.93 0-1.31.465-2.38 1.235-3.22-.135-.303-.54-1.523.105-3.176 0 0 1.005-.322 3.3 1.23.96-.267 1.98-.399 3-.405 1.02.006 2.04.138 3 .405 2.28-1.552 3.285-1.23 3.285-1.23.645 1.653.24 2.873.12 3.176.765.84 1.23 1.91 1.23 3.22 0 4.61-2.805 5.625-5.475 5.92.42.36.81 1.096.81 2.22 0 1.606-.015 2.896-.015 3.286 0 .315.21.69.825.57C20.565 22.092 24 17.592 24 12.297c0-6.627-5.373-12-12-12"/></svg>'
    ),
}

# Schemes accepted for report_info.json link hrefs. The file is trusted repo
# content, but the report is published, so anything else is dropped rather than
# emitted into a public page.
SAFE_LINK_SCHEMES = ("http://", "https://", "mailto:")

JSONBLOB_RE = re.compile(r'data-jsonblob="([^"]+)"')

# Matches a parametrized interop test id, e.g.
#   wallets/hovi/tests/test_credential_issuance.py::test_credential_issuance[
#       authbound_issuer/credential_issuance-hovi]
#
# A wallet may add its own parametrize dimension on top of the shared
# issuer/case/driver ones — gataca runs each case once per DID method, giving
# ids that end "-gataca-jwk" / "-gataca-ebsi". The trailing token after the
# wallet name is captured as "variant" and gets its own matrix column, so a
# method that passes is not hidden behind one that fails. "case" is lazy so it
# stops at the first occurrence of the wallet name rather than swallowing it.
TESTID_RE = re.compile(
    r"wallets/(?P<wallet>[^/]+)/tests/test_credential_(?P<flow>issuance|verification)\.py"
    r"::test_credential_(?:issuance|verification)\["
    r"(?P<agent>[^/\]]+)/(?P<case>.+?)-(?P=wallet)"
    r"(?:-(?P<variant>[^\]]+))?\]"
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
    """Split an interop test id into (wallet, flow, agent, case, variant).

    ``variant`` is None for wallets that only run the shared parametrize
    dimensions, and the extra trailing token otherwise ("jwk", "ebsi", ...).
    Returns None for ids this report does not chart — lifecycle tests such as
    test_install / test_onboarding / test_cleanup live in the per-wallet
    pytest-html reports only.
    """
    match = TESTID_RE.search(test_id)
    if not match:
        return None
    return match["wallet"], match["flow"], match["agent"], match["case"], match["variant"]


# Brand-specific name spellings that don't follow simple capitalization.
# Tokens not listed here are title-cased ("hovi_issuer" -> "Hovi Issuer").
DISPLAY_OVERRIDES = {"waltid": "walt.id"}


def display_name(name: str) -> str:
    """Human-readable label for a wallet or agent key.

    Underscores become spaces and each token is capitalized, so
    "hovi_issuer" -> "Hovi Issuer" and "heidi" -> "Heidi". Tokens listed in
    DISPLAY_OVERRIDES keep their brand spelling ("waltid" -> "walt.id").
    Only affects displayed text — icon lookups and JSON keys use the raw name.
    """
    parts = [p for p in name.split("_") if p]
    return " ".join(DISPLAY_OVERRIDES.get(p, p.capitalize()) for p in parts)


def column_key(wallet: str, variant: Optional[str]) -> str:
    """Identity of one matrix column: the wallet, or a wallet+variant pair."""
    return f"{wallet}-{variant}" if variant else wallet


def column_label(wallet: str, variant: Optional[str]) -> str:
    """Displayed column header, e.g. "Hovi" or "Gataca (jwk)".

    Variants keep their raw spelling — they are DID method names (jwk, ebsi)
    where lowercase is the correct form.
    """
    base = display_name(wallet)
    return f"{base} ({variant})" if variant else base


def is_wallet_dir(child: Path) -> bool:
    """True if a run subdirectory holds one wallet's results.

    A wallet that crashed before pytest could write report.html still leaves
    logs and screenshots behind. Recognising it by name lets the matrix show a
    "no data" column instead of dropping the wallet without a trace.
    """
    if not child.is_dir():
        return False
    return (child / "report.html").exists() or (REPO_ROOT / "wallets" / child.name).is_dir()


def collect(run_dir: Path) -> dict:
    # Keyed by column key ("hovi", "gataca-jwk"), not by wallet, so each
    # parametrize variant keeps its own results.
    issuance: dict[str, dict[str, str]] = {}
    verification: dict[str, dict[str, str]] = {}
    issuance_agents: set[str] = set()
    verification_agents: set[str] = set()
    totals = {"Passed": 0, "Failed": 0, "Error": 0, "Skipped": 0}
    # wallet -> variants seen in this run; empty set means "no extra dimension"
    variants: dict[str, set[str]] = {}
    no_report: list[str] = []
    wallet_order: list[str] = []

    for child in sorted(run_dir.iterdir()):
        if not is_wallet_dir(child):
            continue
        wallet = child.name
        wallet_order.append(wallet)
        variants.setdefault(wallet, set())

        report = child / "report.html"
        if not report.exists():
            no_report.append(wallet)
            continue

        data = load_pytest_html(report)
        for tid, recs in data.get("tests", {}).items():
            rec = recs[-1] if isinstance(recs, list) and recs else (recs if isinstance(recs, dict) else {})
            outcome = rec.get("result") if isinstance(rec, dict) else None
            parsed = parse_test_id(tid)
            if not parsed:
                continue
            _, flow, agent, _case, variant = parsed
            if variant:
                variants[wallet].add(variant)
            col = column_key(wallet, variant)
            if flow == "issuance":
                target, agents_set = issuance, issuance_agents
            else:
                target, agents_set = verification, verification_agents
            agents_set.add(agent)
            cell = target.setdefault(col, {})
            existing = cell.get(agent)
            # Worst outcome wins: several cases can share one agent cell, and a
            # rerun must not let a later pass mask an earlier failure.
            if OUTCOME_RANK.get(outcome, 0) >= OUTCOME_RANK.get(existing, 0):
                cell[agent] = outcome
            if outcome in totals:
                totals[outcome] += 1

    columns = []
    for wallet in wallet_order:
        for variant in (sorted(variants[wallet]) or [None]):
            columns.append({
                "key": column_key(wallet, variant),
                "wallet": wallet,
                "variant": variant,
                "label": column_label(wallet, variant),
                "no_report": wallet in no_report,
            })

    return {
        "run_ts": run_dir.name,
        "columns": columns,
        # Column keys, in display order. Named "wallets" for continuity with
        # earlier runs, where every column was exactly one wallet.
        "wallets": [c["key"] for c in columns],
        "no_report": no_report,
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
  --ink-2: #374151;
  --surface: #ffffff;
  --link: #2563eb;
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
/* A wallet whose run produced no report.html: its column is data-less, not
   a column of genuinely untested pairs. Muted so the two don't look alike. */
th.no-data .label > span { color: var(--muted); font-weight: 500; font-style: italic; }
th.no-data .icon { opacity: .45; }
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
  .info-section { grid-template-columns: 1fr; margin: 18px; }
}

/* ── Info section (about / status key / reading the matrix) ─────── */
.info-section {
  margin: 24px 28px 6px;
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 1px;
  background: var(--line);
  border: 1px solid var(--line);
  border-radius: 10px;
  overflow: hidden;
}
.info-block { background: var(--surface); padding: 22px 24px; }
.info-title {
  font-size: .68rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: .1em;
  color: var(--muted);
  margin-bottom: 10px;
}
.info-block p { margin: 0; font-size: .84rem; color: var(--ink-2); line-height: 1.7; }
.info-block a { color: var(--link); text-underline-offset: 2px; }
.repo-links { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 14px; }
.repo-link {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  padding: 5px 12px;
  border-radius: 6px;
  border: 1px solid var(--line-strong);
  background: #f9fafb;
  color: var(--ink);
  font-size: .78rem;
  font-weight: 500;
  text-decoration: none;
  transition: border-color .15s, background .15s;
}
.repo-link:hover { background: #f3f4f6; border-color: #9ca3af; }
.repo-link svg { width: 15px; height: 15px; flex: none; color: var(--ink); fill: currentColor; }
.legend { list-style: none; padding: 0; margin: 0; display: flex; flex-direction: column; gap: 9px; }
.legend li { display: flex; align-items: flex-start; gap: 9px; font-size: .84rem; color: var(--ink-2); line-height: 1.5; }
.legend li svg { flex: none; margin-top: 1px; }
/* The key reuses the matrix cell glyphs (22px by default); the legend
   wants them a little smaller alongside body text. */
.legend li .g { width: 18px; height: 18px; }
.legend li strong { font-weight: 600; color: var(--ink); }
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
    {info}
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


def _agent_row(agent: str, col_keys: list, data: dict, output_dir: Path, embed: bool) -> str:
    head = (
        "<td class='row-head'><div class='label'>"
        + icon_html(agent, "agents", output_dir, embed)
        + f"<span>{html.escape(display_name(agent))}</span></div></td>"
    )
    cells = "".join(_outcome_td(data.get(k, {}).get(agent)) for k in col_keys)
    return f"<tr>{head}{cells}</tr>"


def render_combined_table(matrix: dict, output_dir: Path, embed: bool) -> str:
    issuance_agents = matrix["issuance_agents"]
    verification_agents = matrix["verification_agents"]
    columns = matrix["columns"]
    wallets = matrix["wallets"]
    issuance = matrix["issuance"]
    verification = matrix["verification"]

    if not issuance_agents and not verification_agents:
        return "<p class='empty'>No issuance or verification tests found in this run.</p>"

    if not wallets:
        return "<p class='empty'>No wallets found in this run.</p>"

    # Column headers: blank corner + one column per wallet (or wallet variant).
    # The icon is looked up by wallet name so every variant of a wallet shares
    # its logo, while the label carries the variant.
    head_cells = ["<th class='row-head'></th>"]
    for col in columns:
        note = " — no pytest report for this run" if col["no_report"] else ""
        cls = " class='no-data'" if col["no_report"] else ""
        head_cells.append(
            f"<th{cls} title=\"{html.escape(col['label'] + note, quote=True)}\">"
            + "<div class='label'>"
            + icon_html(col["wallet"], "wallets", output_dir, embed)
            + f"<span>{html.escape(col['label'])}</span></div></th>"
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


def load_report_info(path: Path) -> Optional[dict]:
    """Read the explanatory blocks from a report_info.json file.

    Returns None when the file is absent — the report still renders, just
    without the info cards — but says so on stderr, since a silently missing
    status key on a published page is easy to overlook. Malformed JSON is a
    mistake rather than a choice, so it aborts.
    """
    if not path.exists():
        hint = ""
        template = path.parent / INFO_TEMPLATE
        if template.exists():
            hint = f" (copy {template.name} to {path.name} to add them)"
        print(f"note: no {path.name} in {path.parent}; "
              f"rendering without info section{hint}", file=sys.stderr)
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        sys.exit(f"Invalid JSON in {path}: {exc}")


def _body_html(body) -> str:
    """Join a block body into one paragraph.

    Accepts a string, or a list of strings joined with single spaces so long
    prose can be line-wrapped in the JSON. Inserted as raw HTML so inline tags
    in the source text keep working.
    """
    if isinstance(body, (list, tuple)):
        text = " ".join(str(part) for part in body)
    else:
        text = str(body)
    return f"<p>{text}</p>"


def _links_html(links) -> str:
    parts = []
    for link in links:
        href = str(link.get("href", ""))
        label = str(link.get("label", ""))
        if not href.startswith(SAFE_LINK_SCHEMES):
            print(f"note: skipping link with unsupported href {href!r}", file=sys.stderr)
            continue
        glyph = LINK_ICONS.get(link.get("icon", ""), "")
        parts.append(
            f'<a class="repo-link" href="{html.escape(href, quote=True)}"'
            ' target="_blank" rel="noopener">'
            f'{glyph}{html.escape(label)}</a>'
        )
    if not parts:
        return ""
    return f'<div class="repo-links">{"".join(parts)}</div>'


def _legend_html(legend) -> str:
    """Status key rows, reusing the glyphs the matrix cells render.

    Pulling from OUTCOME_SVG rather than repeating the shapes means the key can
    never drift from the table it explains.
    """
    items = []
    for entry in legend:
        outcome = entry.get("outcome")
        glyph = OUTCOME_SVG.get(outcome, SVG_NONE)
        term = html.escape(str(entry.get("term", outcome or "")))
        desc = html.escape(str(entry.get("description", "")))
        items.append(
            f"<li>{glyph}<span><strong>{term}</strong> — {desc}</span></li>"
        )
    if not items:
        return ""
    return f'<ul class="legend">{"".join(items)}</ul>'


def render_info_section(info: Optional[dict]) -> str:
    """Build the explanatory cards under the matrix from report_info.json."""
    if not info:
        return ""
    blocks = []
    for block in info.get("blocks", []):
        inner = [
            f'<div class="info-title">{html.escape(str(block.get("title", "")))}</div>'
        ]
        if block.get("body"):
            inner.append(_body_html(block["body"]))
        if block.get("legend"):
            inner.append(_legend_html(block["legend"]))
        if block.get("links"):
            inner.append(_links_html(block["links"]))
        blocks.append(f'<div class="info-block">{"".join(inner)}</div>')
    if not blocks:
        return ""
    return f'<div class="info-section">{"".join(blocks)}</div>'


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


def render_html(matrix: dict, output_dir: Path, embed: bool,
                info: Optional[dict] = None) -> str:
    sections = render_combined_table(matrix, output_dir, embed)
    return PAGE.format(
        run_ts=html.escape(matrix["run_ts"]),
        css=CSS,
        logo=_logo_html(output_dir, embed),
        pills=_pills_html(matrix["totals"]),
        sections=sections,
        info=render_info_section(info),
        generated=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("run_dir", help="Path to a reports/<timestamp> directory")
    ap.add_argument("--output", default=str(DEFAULT_OUTPUT),
                    help="Output directory (default: <repo>/status/)")
    ap.add_argument("--embed-icons", action="store_true",
                    help="Inline icons as base64 data URIs for a single-file HTML")
    ap.add_argument("--info", default=None,
                    help="Explanatory blocks JSON (default: <output>/.report_info.json)")
    ap.add_argument("--no-info", action="store_true",
                    help="Render the matrix alone, without the explanatory blocks")
    args = ap.parse_args(argv)

    run_dir = resolve_run_dir(args.run_dir)
    output_dir = Path(args.output)
    if not output_dir.is_absolute():
        output_dir = (Path.cwd() / output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    # Resolved after output_dir: the info file ships with the published site,
    # so it follows --output the same way icons/ does.
    info_path = Path(args.info) if args.info else output_dir / INFO_FILENAME
    info = None if args.no_info else load_report_info(info_path)
    matrix = collect(run_dir)
    page = render_html(matrix, output_dir, args.embed_icons, info)
    (output_dir / "index.html").write_text(page, encoding="utf-8")
    (output_dir / "data.json").write_text(json.dumps(matrix, indent=2), encoding="utf-8")
    print(f"wrote {output_dir / 'index.html'}")
    print(f"wrote {output_dir / 'data.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
