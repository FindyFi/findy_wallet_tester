#!/usr/bin/env python3
"""Generate a compact wallet x agent interop matrix from a pytest run.

Reads pytest-html report.html files inside a given run directory and emits
a self-contained, embeddable HTML report (plus a JSON twin) under status/.

Rows are agents (issuers / verifiers), columns are wallets. A wallet that adds
its own parametrize dimension gets one column per value, so gataca's DID
methods appear as "Gataca (jwk)" and "Gataca (ebsi)" rather than collapsing
into a single cell. Only the issuance and verification suites are charted;
install / onboarding / cleanup results stay in the per-wallet pytest reports.

A failing cell says *why* it failed. The flows tag their exceptions with a
category from a shared vocabulary ("[unroutable]", "[rejected]", ...); this
script reads the tag out of the pytest log, falls back to matching known
message shapes for the flows that have not adopted it, and prints a short
reason under the cell's mark. An unrecognised failure still renders as a plain
red cell, exactly as every failure did before. See FAILURE_CATEGORIES.

A green cell that only went green on a retry says so too: pytest-rerunfailures
records every attempt, and "passes" and "passes on the third try" are not the
same finding.

Column headers carry the build under test, and a "Builds under test" table
under the matrix gives the package, version and device per wallet — read from
the app_info.json each wallet's run already writes.

The explanatory blocks under the matrix (about / status key / how to read it)
are content, not code: they live in .report_info.json inside the output
directory, alongside index.html and icons/. That file is gitignored so the
wording stays out of versioning; report_info.example.json beside it documents
the shape. Missing file means the report renders without the info blocks. The
reason key is *generated* rather than authored, so it can never describe a
category the matrix does not show.

Each run is recorded in .report_history.json in the output directory: a short
entry with the tallies, the reasons, and the build and device each wallet was
tested on. The page marks every cell that reads differently from the previous
recorded run.

Normally one generate adds one entry. --backfill instead scans for past runs
and records all of them first, which is how a history gets started from an
archive that predates this file. Runs are judged by what they chart, not by
what files they hold: --min-cells keeps single-wallet debug runs and half
runs out, since one of those beside a full run reads as a collapse.

The strip of past runs lists only the runs that changed something — the
passing count moved, the run covered a wallet or agent the one before it did
not, or a wallet shipped a new build. Runs that repeated the previous result
are counted at the foot instead. A build change counts even when the version
name is unchanged, because it often is: hovi shipped 29 -> 34 as 1.3.0 both
times. The
file is dot-prefixed and gitignored like the info file, so it is never pushed
to the public site; being ignored is also what keeps git checkouts from
touching it. HISTORY_LIMIT bounds how many runs it keeps.

The JSON twin (data.json) carries all of this: each cell is an object with
"outcome", "reason", "detail", "attempts", "flaky" and "change" rather than a
bare outcome string, and the top level gains "reason_counts", "flaky_cells",
"wallet_info", "compared_to" and "change_counts".

Usage:
    python generate_compact_report.py reports/2026-05-04_10-13-21
    python generate_compact_report.py reports/<run> --embed-icons
    python generate_compact_report.py reports/<run> --output some/dir
    python generate_compact_report.py reports/<run> --info other/info.json
    python generate_compact_report.py reports/<run> --no-info
    python generate_compact_report.py reports/<run> --no-history
    python generate_compact_report.py reports/<run> --backfill
    python generate_compact_report.py reports/<run> --backfill --scan other/reports
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

# Run history, kept beside the info file and treated the same way: it lives
# with the published site but is dot-prefixed and gitignored, so it is never
# pushed to the public repo. Being ignored is also what makes it durable —
# git checkouts, pulls and branch switches leave it alone. A fresh clone on
# another machine starts with no history, which is the accepted trade-off for
# keeping raw per-run metadata unpublished.
HISTORY_FILENAME = ".report_history.json"
HISTORY_LIMIT = 30
# How many past runs the on-page strip shows. The file keeps more than the
# page displays, so the record outlives the summary.
HISTORY_SHOWN = 8

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

# ---------------------------------------------------------------------------
# Failure reasons
# ---------------------------------------------------------------------------
# A red cell that only says "Failed" is not an actionable finding: "your URL
# never reached this wallet" and "this wallet rejected your credential" point
# at opposite parties. These are the shared outcome names the flows already
# raise, plus "harness" for the case where the test itself fell over.
#
# label       shown in the reason key under the matrix
# short       the caption printed inside a matrix cell (kept narrow on purpose)
# description the key entry; written for a reader choosing between providers
FAILURE_CATEGORIES = {
    "unroutable": {
        "stage": "deliver",
        "label": "Unroutable",
        "short": "unroutable",
        "description": "The link never reached the wallet. Android had no app registered "
                       "for the URL the issuer or verifier handed out, so the wallet never "
                       "saw the offer or request at all.",
    },
    "rejected": {
        "stage": "accept",
        "label": "Rejected",
        "short": "rejected",
        "description": "The wallet received the offer or request and refused it, showing "
                       "an error of its own.",
    },
    "no_match": {
        "stage": "match",
        "label": "No match",
        "short": "no match",
        "description": "The wallet showed the request and answered that it holds nothing "
                       "satisfying it, even though it is not empty — a disagreement about "
                       "credential types.",
    },
    "nothing_to_present": {
        "stage": "match",
        "label": "Nothing to present",
        "short": "nothing held",
        "description": "Verification ran against a wallet holding no suitable credential, "
                       "so there was nothing to share.",
    },
    "not_stored": {
        "stage": "keep",
        "label": "Not stored",
        "short": "not stored",
        "description": "The wallet accepted the credential, but its credential count never "
                       "moved — nothing was kept.",
    },
    "dismissed": {
        "stage": "present",
        "label": "Dismissed",
        "short": "dismissed",
        "description": "The wallet returned to its home screen without ever offering "
                       "anything to accept or share.",
    },
    "absent": {
        "stage": "open",
        "label": "Absent",
        "short": "absent",
        "description": "The wallet never came to the foreground.",
    },
    "processing": {
        "stage": "present",
        "label": "Timed out",
        "short": "timed out",
        "description": "The wallet was still working when the wait ran out. No verdict was "
                       "reached either way.",
    },
    "harness": {
        # No stage: the flow never started, so it cannot have stopped in one.
        "stage": None,
        "label": "Test setup",
        "short": "setup",
        "description": "The test setup failed before the flow could run. The cell says "
                       "nothing about interoperability.",
    },
}

# What a cell's corner mark means, relative to the previous recorded run. Set
# by annotate_changes(); a cell that reads the same as last time gets no mark.
#
# label      the key entry's heading
# tooltip    the sentence appended to the cell's own tooltip
# key        the key entry's prose, which must not repeat the label
CHANGES = {
    "broke": {
        "glyph": "\u2193",  # down: it used to pass
        "label": "Newly failing",
        "tooltip": "Newly failing — this passed in the previous run.",
        "key": "The pair passed in the previous run and does not now.",
    },
    "fixed": {
        "glyph": "\u2191",  # up: it used to fail
        "label": "Newly passing",
        "tooltip": "Newly passing — this failed in the previous run.",
        "key": "The pair failed in the previous run and passes now.",
    },
    "reason": {
        "glyph": "\u2192",  # sideways: red either way, but at a different step
        "label": "Different reason",
        "tooltip": "Still failing, but for a different reason than last run.",
        "key": "Red in both runs, but the flow broke down at a different point.",
    },
    "new": {
        "glyph": "+",  # no direction to show — there is nothing to compare
        "label": "First result",
        "tooltip": "First result for this pair.",
        "key": "No previous run covered this pair, so there is nothing to compare.",
    },
}

# The flow both suites share, generalised to the steps a wallet must get
# through. Every failure category names the step it stopped at (see "stage"
# above), so the diagram and the cell captions cannot disagree — they are the
# same data read two ways.
#
# caption spells out what the step means in each suite where the two differ;
# the wording is what a reader needs to place a red cell on the line.
# label and caption may be one string, or a per-suite dict where the two
# flows genuinely differ — an issuance link carries an offer, a verification
# link carries a request, and saying so beats one wording that fits neither.
# "suites" limits a step to the flows it belongs to; a flow that has no such
# step simply does not draw it.
FLOW_STAGES = (
    ("deliver", {
        "label": {"issuance": "Offer link delivered",
                  "verification": "Request link delivered"},
        "caption": {"issuance": "the issuer's URL reaches the wallet",
                    "verification": "the verifier's URL reaches the wallet"},
    }),
    ("open", {
        "label": "Wallet opens",
        "caption": "the app comes to the foreground",
    }),
    ("present", {
        "label": {"issuance": "Offer shown", "verification": "Request shown"},
        "caption": {"issuance": "the wallet displays what is on offer",
                    "verification": "the wallet displays what is asked of it"},
    }),
    ("match", {
        "label": "Match found",
        "caption": "the wallet holds a credential that satisfies the request",
        # Issuance has nothing to match against: the credential is inbound.
        "suites": ("verification",),
    }),
    ("accept", {
        "label": "Accepted",
        "caption": {"issuance": "the wallet takes the credential rather than erroring out",
                    "verification": "the wallet hands the presentation over rather than erroring out"},
    }),
    ("keep", {
        "label": {"issuance": "Credential stored", "verification": "Presentation shared"},
        "caption": {"issuance": "the wallet's credential count moves",
                    "verification": "the presentation reaches the verifier"},
    }),
)

SUITES = (("issuance", "Issuance"), ("verification", "Verification"))


def stage_text(meta: dict, field: str, suite: str) -> str:
    """One stage's label or caption for a given suite.

    Accepts a plain string for the steps that read the same either way, or a
    per-suite dict for the ones that do not.
    """
    value = meta.get(field, "")
    if isinstance(value, dict):
        return value.get(suite, "")
    return value


def stage_applies(meta: dict, suite: str) -> bool:
    """Whether a step is part of this suite's flow at all."""
    return suite in meta.get("suites", tuple(k for k, _ in SUITES))


# Precedence when several test cases share one matrix cell. Highest wins, in
# the same spirit as OUTCOME_RANK: prefer the reason that names a concrete
# party. "unroutable" tops the list because it is the most definitive — the
# flow never started. "harness" sits at the bottom: a real interop reason from
# a sibling case is always worth more than our own setup falling over.
CATEGORY_RANK = {
    None: 0, "harness": 10, "processing": 20, "absent": 30, "dismissed": 35,
    "not_stored": 40, "nothing_to_present": 45, "no_match": 50,
    "rejected": 60, "unroutable": 70,
}

# The flows tag their exceptions with the category in brackets, e.g.
#   base.outcome.FlowFailure: [unroutable] [credential_flow] Could not deliver...
#   AssertionError: [not_stored] Credential 'x' from 'y' did not...
# Only a bracket immediately after the exception name counts, and only when the
# word is one we know: several messages carry a *flow* name in brackets
# ("[credential_flow]", "[after consent]") which must never be read as a
# category.
TYPED_CATEGORY_RE = re.compile(
    r"^[\w.]*(?:Error|Exception|Failure|Failed):\s*\[([a-z_]+)\]"
)

# The exception headline inside a pytest failure block: a line starting with
# "E" whose text opens with an exception class or a bare assert. Skips the
# stack frames, source echo and assertion diffs that also carry the E prefix.
EXC_LINE_RE = re.compile(r"^E\s+((?:[\w.]*(?:Error|Exception|Failure|Failed)\b|assert\s).*)$")

# Fallback for flows that have not adopted the typed exception yet. Matched
# against the exception headlines only, never the whole log — captured log
# output is full of the word "error" and matching it wholesale mislabels cells.
#
# Order matters: the first hit wins, so the specific phrasings come before the
# catch-all "the wallet showed an error" ones. Message text becoming a contract
# is the known cost of reading this out of prose; when a run grows an
# outcomes.json sidecar, prefer that and keep this as the fallback.
REASON_PATTERNS = (
    # Appium could not hand the URL to any app: Android resolved no activity.
    ("unroutable", r"Activity not started, unable to resolve Intent|Could not deliver\b"),
    ("no_match", r"No matching credentials"),
    ("nothing_to_present", r"does not have the cards required|UNAVAILABLE CARDS"),
    ("dismissed", r"returned to (?:its )?home (?:screen )?without"),
    # Wallet-agnostic setup assertions — gataca checks the active DID method
    # before the flow starts, and that failing is our problem, not the agent's.
    ("harness", r"Active DID is "),
    ("processing", r"did not appear within|did not complete within timeout"
                   r"|No offer/error/home screen after|No success/home screen after"
                   r"|^selenium\.common\.exceptions\.TimeoutException"),
    # The wallet surfaced an error of its own. The wrapper messages
    # ("Credential issuance failed for 'x': <wallet's error text>") land here
    # too: the flow only raises them after reading an error screen.
    ("rejected", r"error dialog|error screen|was rejected|Error in OpenID4VCI"
                 r"|status code is error|error response with status"
                 r"|Credential issuance failed|Verification failed for"),
    ("not_stored", r"^assert\s"),
)

# Trailing marker the rerun plugin reads; noise in a published tooltip.
NO_RETRY_RE = re.compile(r"\s*\[no_retry\]\s*$")
DETAIL_MAX = 200


def exception_headlines(log: str) -> list:
    """The exception lines of a pytest failure block, outermost first."""
    return [m.group(1).strip() for m in
            (EXC_LINE_RE.match(line) for line in log.splitlines()) if m]


def classify_failure(log: Optional[str]):
    """Work out *why* a test failed. Returns (category, detail headline).

    Prefers the category the flow tagged onto its exception; falls back to
    matching known message shapes. Either may come back None — an unclassified
    failure renders as a plain red cell, which is what the report did for every
    failure before this existed.
    """
    if not log:
        return None, ""
    headlines = exception_headlines(html.unescape(log))
    if not headlines:
        return None, ""

    # The detail shown is the line that justified the reason, not simply the
    # first one. A chained traceback leads with the low-level cause (an Appium
    # WebDriverException, say) and ends with the flow's own diagnosis, which is
    # the sentence worth publishing.
    for headline in headlines:
        typed = TYPED_CATEGORY_RE.match(headline)
        if typed and typed.group(1) in FAILURE_CATEGORIES:
            return typed.group(1), _detail(headline)

    for category, pattern in REASON_PATTERNS:
        for headline in headlines:
            if re.search(pattern, headline, re.IGNORECASE):
                return category, _detail(headline)
    return None, _detail(headlines[0])


def _detail(headline: str) -> str:
    """Tidy one exception headline for a tooltip."""
    text = NO_RETRY_RE.sub("", headline).strip()
    if len(text) > DETAIL_MAX:
        text = text[:DETAIL_MAX - 1].rstrip() + "\u2026"
    return text


def load_app_info(wallet_dir: Path) -> dict:
    """Per-wallet build and device facts, written by the run itself.

    A matrix without versions is hard to act on: "hovi fails" means nothing
    without "hovi 1.3.0 (34), on a moto g24 running Android 14". Missing or
    unreadable files are not fatal — the column just renders without them.
    """
    path = wallet_dir / "app_info.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        print(f"note: ignoring unreadable {path}: {exc}", file=sys.stderr)
        return {}
    return data if isinstance(data, dict) else {}


def version_label(info: dict) -> str:
    """Short build string for a column header, e.g. "1.3.0 (34)"."""
    name = str(info.get("version_name") or "").strip()
    code = str(info.get("version_code") or "").strip()
    if name and code:
        return f"{name} ({code})"
    return name or (f"build {code}" if code else "")


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


def make_cell(recs: list) -> dict:
    """Fold one test id's attempts into the facts a matrix cell shows.

    pytest-rerunfailures records every attempt, so ``recs`` may hold two
    discarded reruns before the verdict. Only the last attempt decides the
    outcome, but the earlier ones are the difference between "passes" and
    "passes on the third try" — worth publishing, and invisible until now.
    """
    last = recs[-1] if recs else {}
    outcome = last.get("result")
    attempts = len(recs)
    reason, detail = (None, "")
    if outcome not in ("Passed", "Skipped", None):
        reason, detail = classify_failure(last.get("log"))
    return {
        "outcome": outcome,
        "reason": reason,
        "detail": detail,
        "attempts": attempts,
        # A pass that needed a retry. The wallet did interoperate, so the cell
        # stays green, but a reader deciding on a provider should see it.
        "flaky": outcome == "Passed" and attempts > 1,
    }


def merge_cells(existing: Optional[dict], incoming: dict) -> dict:
    """Combine two test cases that land in the same wallet x agent cell.

    Worst outcome wins, as before. When both carry the same outcome the more
    specific reason wins (CATEGORY_RANK) rather than the last one read —
    last-writer-wins would let test ordering decide what the report says.
    """
    if existing is None:
        return incoming
    rank_new = OUTCOME_RANK.get(incoming["outcome"], 0)
    rank_old = OUTCOME_RANK.get(existing["outcome"], 0)
    if rank_new > rank_old:
        merged = dict(incoming)
    elif rank_new < rank_old:
        merged = dict(existing)
    elif CATEGORY_RANK.get(incoming["reason"], 0) > CATEGORY_RANK.get(existing["reason"], 0):
        merged = dict(incoming)
    else:
        merged = dict(existing)
    merged["attempts"] = max(existing["attempts"], incoming["attempts"])
    merged["flaky"] = existing["flaky"] or incoming["flaky"]
    return merged


def collect(run_dir: Path) -> dict:
    # Keyed by column key ("hovi", "gataca-jwk"), not by wallet, so each
    # parametrize variant keeps its own results. Each value is a cell dict
    # from make_cell(), not a bare outcome string.
    issuance: dict[str, dict[str, dict]] = {}
    verification: dict[str, dict[str, dict]] = {}
    issuance_agents: set = set()
    verification_agents: set = set()
    totals = {"Passed": 0, "Failed": 0, "Error": 0, "Skipped": 0}
    # wallet -> variants seen in this run; empty set means "no extra dimension"
    variants: dict[str, set] = {}
    no_report: list = []
    wallet_order: list = []
    wallet_info: dict[str, dict] = {}

    for child in sorted(run_dir.iterdir()):
        if not is_wallet_dir(child):
            continue
        wallet = child.name
        wallet_order.append(wallet)
        variants.setdefault(wallet, set())
        # Read before the report check: a wallet that crashed before pytest
        # wrote report.html may still have recorded which build it installed.
        info = load_app_info(child)
        if info:
            wallet_info[wallet] = info

        report = child / "report.html"
        if not report.exists():
            no_report.append(wallet)
            continue

        data = load_pytest_html(report)
        for tid, recs in data.get("tests", {}).items():
            if isinstance(recs, dict):
                recs = [recs]
            elif not isinstance(recs, list) or not recs:
                continue
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
            cell = make_cell(recs)
            bucket = target.setdefault(col, {})
            bucket[agent] = merge_cells(bucket.get(agent), cell)
            if cell["outcome"] in totals:
                totals[cell["outcome"]] += 1

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

    # Counted over cells rather than tests: the key under the matrix explains
    # what a reader can see, and a reader sees cells.
    reason_counts: dict = {}
    flaky_cells = 0
    for section in (issuance, verification):
        for agents in section.values():
            for cell in agents.values():
                if cell["reason"]:
                    reason_counts[cell["reason"]] = reason_counts.get(cell["reason"], 0) + 1
                if cell["flaky"]:
                    flaky_cells += 1

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
        "reason_counts": reason_counts,
        "flaky_cells": flaky_cells,
        "wallet_info": wallet_info,
    }


# ---------------------------------------------------------------------------
# Run history
# ---------------------------------------------------------------------------
# One matrix is a snapshot; the question a reader actually has is "what changed
# since last time". Each run appends a compact entry here — enough to tell what
# flipped and what was under test, not a second copy of the report.


def _history_cells(matrix: dict) -> dict:
    """The run's outcomes, flattened to the smallest form a diff needs.

    "Passed", or "Failed:rejected" — outcome and reason in one token, so a
    stored run stays small enough to keep thirty of them.
    """
    out = {}
    for section in ("issuance", "verification"):
        rows = {}
        for col, agents in matrix.get(section, {}).items():
            for agent, cell in agents.items():
                outcome = cell.get("outcome")
                if not outcome:
                    continue
                token = f"{outcome}:{cell['reason']}" if cell.get("reason") else outcome
                rows.setdefault(col, {})[agent] = token
        if rows:
            out[section] = rows
    return out


def history_entry(matrix: dict) -> dict:
    """The compact record of one run.

    Carries the metadata that makes a past result readable — which build of
    each wallet, on which device — because a tally alone cannot be re-checked
    later against the app it came from.
    """
    wallets = {}
    for wallet, info in (matrix.get("wallet_info") or {}).items():
        entry = {
            "version_name": info.get("version_name"),
            "version_code": info.get("version_code"),
            "package": info.get("package"),
            "device_name": info.get("device_name"),
            "platform": info.get("platform"),
            "platform_version": info.get("platform_version"),
        }
        if info.get("updated"):
            entry["updated_from"] = info.get("version_before")
            entry["updated_to"] = info.get("version_after")
        wallets[wallet] = {k: v for k, v in entry.items() if v not in (None, "")}
    return {
        "run_ts": matrix["run_ts"],
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "totals": dict(matrix.get("totals") or {}),
        "flaky_cells": matrix.get("flaky_cells", 0),
        "reason_counts": dict(matrix.get("reason_counts") or {}),
        "no_report": list(matrix.get("no_report") or []),
        "wallets": wallets,
        "cells": _history_cells(matrix),
    }


def load_history(path: Path) -> list:
    """Past run entries, oldest first. A missing or broken file is not fatal.

    History is a convenience layered on top of the report; refusing to publish
    because the log of previous runs got mangled would be the wrong trade. A
    damaged file is reported and set aside rather than silently overwritten.
    """
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return _set_aside(path, f"unreadable: {exc}")
    runs = data.get("runs") if isinstance(data, dict) else data
    if not isinstance(runs, list):
        return _set_aside(path, "unexpected shape")
    return [r for r in runs if isinstance(r, dict) and r.get("run_ts")]


def _set_aside(path: Path, why: str) -> list:
    """Move a damaged history out of the way and carry on with a fresh one.

    The run about to be recorded must not be blocked by a bad file, but the
    bad file is the only copy of everything before it — so it is renamed, not
    overwritten, and can be repaired by hand afterwards.
    """
    spoiled = path.with_name(path.name + ".corrupt")
    try:
        path.replace(spoiled)
        print(f"note: {path.name} is {why}; kept it as {spoiled.name} "
              f"and started a fresh history", file=sys.stderr)
    except OSError as exc:
        print(f"note: {path.name} is {why} and could not be set aside ({exc}); "
              f"it will be overwritten", file=sys.stderr)
    return []


def update_history(history: list, entry: dict, limit: int = HISTORY_LIMIT) -> list:
    """Fold this run into the history, oldest first, capped at ``limit``.

    Re-generating a report for a run already recorded replaces that entry
    rather than adding a duplicate, so re-running the script is safe.
    """
    kept = [r for r in history if r.get("run_ts") != entry["run_ts"]]
    kept.append(entry)
    # run_ts is the run directory name, which sorts chronologically by
    # construction ("2026-09-09_11-10-03").
    kept.sort(key=lambda r: str(r.get("run_ts")))
    return kept[-limit:] if limit and limit > 0 else kept


def write_history(path: Path, runs: list) -> None:
    """Save the history, replacing the file only once the new one is written.

    The history cannot be reconstructed from anywhere else, so an interrupted
    write must not be able to truncate it.
    """
    payload = {"version": 1, "runs": runs}
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(path)


# A run directory has to chart a real matrix to be worth recording. The
# archive is mostly single-wallet debug runs, and one of them charting four
# cells would sit in the history beside a full run as though the suite had
# collapsed that day.
DEFAULT_MIN_CELLS = 70


def charted_cells(matrix: dict) -> int:
    """How many wallet x agent cells a run actually produced."""
    return sum(len(agents)
               for section in ("issuance", "verification")
               for agents in matrix.get(section, {}).values())


def discover_runs(roots: list, min_cells: int):
    """Run directories worth recording, oldest first.

    Returns (kept, skipped) where each entry is (name, path, matrix, cells).
    Runs are judged by what they chart rather than by what files they have:
    a directory can hold eight report.html files and still contain no
    issuance or verification results at all.
    """
    candidates = {}
    for root in roots:
        if not root.is_dir():
            print(f"note: no such directory to scan: {root}", file=sys.stderr)
            continue
        for child in sorted(root.iterdir()):
            if child.is_dir():
                # Later roots win, so an explicitly named one overrides the
                # default when the same run appears in both.
                candidates[child.name] = child
    kept, skipped = [], []
    for name in sorted(candidates):
        path = candidates[name]
        try:
            matrix = collect(path)
        except Exception as exc:                      # noqa: BLE001 - one bad
            skipped.append((name, path, None, 0))     # run must not stop the scan
            print(f"note: could not read {path}: {exc}", file=sys.stderr)
            continue
        cells = charted_cells(matrix)
        (kept if cells >= min_cells else skipped).append((name, path, matrix, cells))
    return kept, skipped


def backfill_history(history: list, runs: list) -> list:
    """Record every discovered run, oldest first.

    Merged into the existing history rather than replacing it, so entries
    whose run directories have since been deleted survive a rescan.
    """
    for _name, _path, matrix, _cells in runs:
        history = update_history(history, history_entry(matrix))
    return history


def previous_run(history: list, run_ts: str) -> Optional[dict]:
    """The most recent recorded run before this one, if any."""
    earlier = [r for r in history if str(r.get("run_ts")) < str(run_ts)]
    return earlier[-1] if earlier else None


def annotate_changes(matrix: dict, previous: Optional[dict]) -> None:
    """Mark each cell with how it differs from the previous run, in place.

    Sets "change" on a cell to one of:
      broke        it passed last time and does not now
      fixed        it failed last time and passes now
      reason       it failed both times, for a different reason
      new          there is no previous result for this cell
    Cells that read the same as last time are left unmarked — the page should
    draw the eye to what moved, not to everything.
    """
    matrix["compared_to"] = previous.get("run_ts") if previous else None
    matrix["change_counts"] = {}
    if not previous:
        return
    counts = matrix["change_counts"]
    before = previous.get("cells") or {}
    for section in ("issuance", "verification"):
        prev_section = before.get(section) or {}
        for col, agents in matrix.get(section, {}).items():
            for agent, cell in agents.items():
                outcome = cell.get("outcome")
                if not outcome:
                    continue
                was = (prev_section.get(col) or {}).get(agent)
                if was is None:
                    cell["change"] = "new"
                    counts["new"] = counts.get("new", 0) + 1
                    continue
                was_outcome, _, was_reason = was.partition(":")
                passed_before = was_outcome == "Passed"
                passes_now = outcome == "Passed"
                if passed_before and not passes_now:
                    cell["change"] = "broke"
                    counts["broke"] = counts.get("broke", 0) + 1
                elif passes_now and not passed_before:
                    cell["change"] = "fixed"
                    counts["fixed"] = counts.get("fixed", 0) + 1
                elif not passes_now and was_reason != (cell.get("reason") or ""):
                    cell["change"] = "reason"
                    counts["reason"] = counts.get("reason", 0) + 1
                    cell["previous_reason"] = was_reason or None


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
  .view-toggle { order: 3; }
  th.row-head, td.row-head { padding-left: 18px; min-width: 130px; }
  tbody tr td:first-child { padding-left: 18px; }
  tbody tr td:last-child  { padding-right: 18px; }
  th:last-child { padding-right: 18px; }
  .info-section { grid-template-columns: 1fr; margin: 18px; }
  .stages { flex-direction: column; gap: 10px; }
  .stage { text-align: left; padding: 0 0 0 26px; }
  .stage::before { top: 0; bottom: 0; left: 6px; right: auto; width: 2px; height: auto; }
  .stage:first-child::before { left: 6px; top: 7px; }
  .stage:last-child::before  { right: auto; bottom: 50%; }
  .stage .dot { top: 1px; left: 0; transform: none; }
  .stage .s-count { display: inline-block; margin: 0 0 0 8px; }
  .stage .s-count .unit { display: inline; margin-left: 3px; }
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
/* Each column is its own stack, so a card can be placed rather than flowed.
   The 1px gaps over the line-coloured background draw the hairline rules. */
.info-col {
  display: flex;
  flex-direction: column;
  background: var(--surface);
}
.info-section.one-col { grid-template-columns: 1fr; }
.info-block { background: var(--surface); padding: 22px 24px; }
/* Rules between stacked cards. Drawn on the cards rather than as grid gaps so
   that hiding a card in the summary view hides its rule with it. */
.info-block + .info-block { border-top: 1px solid var(--line); }
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

/* ── Failure reason captions in the matrix cells ─────────────────── */
/* The glyph still carries the outcome; the caption only qualifies it, so it
   stays quiet enough that a scan of the table reads as colour first. */
.cell .reason {
  display: block;
  margin-top: 4px;
  font-size: .62rem;
  line-height: 1;
  font-weight: 600;
  letter-spacing: .01em;
  color: var(--muted);
  white-space: nowrap;
}
.cell.fail .reason { color: #a8402c; }
.cell.err .reason  { color: #9a6410; }
/* A pass that needed a retry: green stays, but it is not a clean green. */
.cell.flaky .g-pass circle { fill: #6aa84f; }
.cell.flaky .reason { color: #7a7f45; font-style: italic; }
.pill.flaky { background: rgba(122,127,69,.14); color: #6b7040; }
.pill.flaky svg circle { fill: #6aa84f; }

/* Version line under a wallet column header. */
th .ver {
  display: block;
  margin-top: 3px;
  font-weight: 500;
  font-size: .68rem;
  color: var(--muted);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

/* ── Reason key rows ─────────────────────────────────────────────── */
.reasons { list-style: none; padding: 0; margin: 10px 0 0; display: flex; flex-direction: column; gap: 9px; }
.reasons li { display: flex; align-items: flex-start; gap: 9px; font-size: .84rem; color: var(--ink-2); line-height: 1.5; }
.reasons li strong { font-weight: 600; color: var(--ink); }
.reasons .tag {
  flex: none;
  min-width: 82px;
  text-align: center;
  padding: 2px 7px;
  border-radius: 5px;
  background: #f1f2f4;
  border: 1px solid var(--line);
  font-size: .62rem;
  font-weight: 600;
  color: #a8402c;
  white-space: nowrap;
  margin-top: 2px;
}
.reasons .count { color: var(--muted); font-variant-numeric: tabular-nums; white-space: nowrap; }
/* Samples in the run-history key are the strip's own spans, so they need the
   same right-alignment gutter the table column gives them. */
.reasons.hkey .hkey-sample {
  flex: none;
  min-width: 82px;
  text-align: right;
  padding-right: 4px;
  margin-top: 1px;
}
.reasons.hkey .hkey-sample .hl { margin: 0; }
.reasons.hkey .hkey-sample .hl-up { color: var(--ok); background: none; border: none; padding: 0; font-weight: 700; }
/* The key swatch is the same arrow the cells draw, at the same scale. */
.reasons.changes .chg-key {
  position: static;
  flex: none;
  min-width: 82px;
  text-align: center;
  font-size: .95rem;
}

/* ── Builds under test ───────────────────────────────────────────── */
.provenance { padding: 20px 28px 4px; }
.prov-title {
  font-size: .7rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: .08em;
  color: var(--muted);
  margin-bottom: 8px;
}
.provenance table { width: 100%; font-size: .78rem; }
.provenance th, .provenance td {
  text-align: left;
  padding: 6px 10px 6px 0;
  border-bottom: 1px solid var(--line);
  vertical-align: top;
}
.provenance th { background: none; font-size: .7rem; color: var(--muted); letter-spacing: .02em; }
.provenance td.w { font-weight: 600; color: var(--ink); white-space: nowrap; }
.provenance td.v { font-variant-numeric: tabular-nums; white-space: nowrap; }
.provenance td.p, .provenance td.d { color: var(--muted); }
.provenance td.p { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: .72rem; }
.provenance .note {
  display: block;
  margin-top: 2px;
  font-size: .68rem;
  font-weight: 500;
  color: var(--muted);
  white-space: normal;
}
.provenance .note.upd { color: var(--err); }

/* ── What moved since the previous run ───────────────────────────── */
/* A corner arrow rather than another word in the cell: the reason caption
   already owns the text, and the eye should catch movement by direction. */
.cell.changed { position: relative; }
/* Direction, not decoration: up means this pair started passing since the
   previous run, down means it stopped. Sat in the corner so it never
   displaces the outcome mark. */
.cell .chg {
  position: absolute;
  top: 3px;
  right: 5px;
  font-size: .8rem;
  font-weight: 700;
  line-height: 1;
  color: var(--muted);
}
.cell .chg-broke  { color: var(--fail); }
.cell .chg-fixed  { color: var(--ok); }
.cell .chg-reason { color: var(--err); }
.cell .chg-new    { color: var(--line-strong); }

/* ── Summary / detailed view ─────────────────────────────────────── */
/* The page opens on the summary: the matrix as it always read, marks only.
   Flipping the switch reveals what this run additionally knows — why each
   cell is red, which build it judged, where the flow broke down, and what
   moved since last time. Driven entirely by the checkbox's :checked state so
   the published file needs no script. */
.view-input {
  position: absolute;
  width: 1px;
  height: 1px;
  opacity: 0;
  pointer-events: none;
}
.view-toggle {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  flex: none;
  cursor: pointer;
  user-select: none;
  font-size: .72rem;
  font-weight: 600;
  letter-spacing: .01em;
}
.view-toggle .vt-opt { color: var(--muted); transition: color .15s ease; }
.view-toggle .vt-summary { color: var(--ink); }
.view-toggle .vt-track {
  position: relative;
  width: 34px;
  height: 18px;
  border-radius: 999px;
  background: #dfe2e6;
  border: 1px solid var(--line-strong);
  transition: background .15s ease, border-color .15s ease;
}
.view-toggle .vt-knob {
  position: absolute;
  top: 1px;
  left: 1px;
  width: 14px;
  height: 14px;
  border-radius: 50%;
  background: #fff;
  box-shadow: 0 1px 2px rgba(20,24,32,.25);
  transition: transform .15s ease;
}
.view-input:checked ~ .header .vt-track { background: #3f6ad8; border-color: #3f6ad8; }
.view-input:checked ~ .header .vt-knob { transform: translateX(16px); }
.view-input:checked ~ .header .vt-summary { color: var(--muted); }
.view-input:checked ~ .header .vt-detail { color: var(--ink); }
/* Keyboard users get the same affordance as a focused control. */
.view-input:focus-visible ~ .header .vt-track {
  outline: 2px solid var(--link);
  outline-offset: 2px;
}

/* Detail-only: whole sections, and the extras threaded through the matrix. */
.detail-only { display: none; }
.view-input:checked ~ .detail-only { display: block; }
.cell .reason,
th .ver,
.pill.flaky,
.info-block.generated { display: none; }
.view-input:checked ~ .table-wrap .cell .reason,
.view-input:checked ~ .table-wrap th .ver { display: block; }
.view-input:checked ~ .info-section .info-block.generated { display: block; }
.view-input:checked ~ .header .pill.flaky { display: inline-flex; }
.cell .chg { display: none; }
.view-input:checked ~ .table-wrap .cell .chg { display: block; }

/* ── Where the flow broke down ───────────────────────────────────── */
/* One horizontal pass through the flow both suites share. The track is drawn
   by the nodes themselves, so the line cannot fall out of step with them. */
.flowline { padding: 20px 28px 4px; }
/* One row per suite. They share their columns, so the pair reads down as well
   as across: the same step sits at the same x on both rails. */
.flow-row + .flow-row { margin-top: 18px; }
.flow-suite {
  font-size: .68rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: .08em;
  color: var(--ink-2);
}
.stages {
  display: flex;
  list-style: none;
  margin: 10px 0 0;
  padding: 0;
}
.stage {
  flex: 1 1 0;
  min-width: 0;
  position: relative;
  padding: 22px 6px 0;
  text-align: center;
}
/* Track segment: full width behind each node, trimmed at both ends. */
.stage::before {
  content: "";
  position: absolute;
  top: 8px;
  left: 0;
  right: 0;
  height: 2px;
  background: var(--line-strong);
}
.stage:first-child::before { left: 50%; }
.stage:last-child::before  { right: 50%; }
.stage .dot {
  position: absolute;
  top: 2px;
  left: 50%;
  transform: translateX(-50%);
  width: 14px;
  height: 14px;
  border-radius: 50%;
  background: var(--card);
  border: 2px solid var(--line-strong);
  box-sizing: border-box;
}
.stage.stopped .dot { background: var(--fail); border-color: var(--fail); }
.stage.done .dot    { background: var(--ok);   border-color: var(--ok); }
.stage .s-label {
  display: block;
  font-size: .72rem;
  font-weight: 600;
  color: var(--ink-2);
  line-height: 1.25;
}
.stage .s-count { display: block; margin-top: 5px; line-height: 1.1; }
.stage .s-count .n {
  font-size: .95rem;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  color: var(--fail);
}
.stage.done .s-count .n { color: var(--ok); }
.stage .s-count .n.none { color: var(--line-strong); font-weight: 500; }
.stage .s-count .unit {
  display: block;
  font-size: .6rem;
  font-weight: 600;
  letter-spacing: .04em;
  text-transform: uppercase;
  color: var(--muted);
}
.stage .s-reasons {
  display: block;
  margin-top: 4px;
  font-size: .6rem;
  line-height: 1.3;
  color: var(--muted);
}
.flow-note { margin: 12px 0 0; font-size: .74rem; color: var(--muted); }
/* Cells the lines could not account for. Called out rather than murmured:
   the rails are only the whole story once this number is zero. */
.unplaced-note {
  margin: 16px 0 0;
  padding: 9px 13px;
  border-left: 3px solid var(--err);
  border-radius: 0 6px 6px 0;
  background: rgba(214,138,23,.09);
  font-size: .8rem;
  line-height: 1.45;
  color: var(--ink-2);
}
.unplaced-note strong { color: var(--ink); font-weight: 700; }

/* ── Recent runs ─────────────────────────────────────────────────── */
.history { padding: 18px 28px 4px; }
.history table { width: 100%; font-size: .78rem; }
.history td {
  padding: 6px 10px 6px 0;
  border-bottom: 1px solid var(--line);
  vertical-align: middle;
  text-align: left;
}
.history tr.current td { background: #fbfcfd; }
.history td.when { white-space: nowrap; color: var(--ink-2); font-variant-numeric: tabular-nums; }
.history .tag-now {
  margin-left: 8px;
  padding: 1px 6px;
  border-radius: 4px;
  background: #eef0f3;
  color: var(--muted);
  font-size: .62rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: .06em;
}
.history td.tally { white-space: nowrap; font-variant-numeric: tabular-nums; font-weight: 600; width: 1%; }
.history td.tally .ok { color: var(--ok); }
.history td.tally .fail { color: var(--fail); }
.history td.tally .sep { color: var(--line-strong); margin: 0 3px; }
.history td.bar { width: 34%; }
.history td.bar { position: relative; }
.history td.bar::before {
  content: "";
  display: block;
  height: 6px;
  border-radius: 999px;
  background: #eceef1;
}
.history td.bar .fill {
  position: absolute;
  left: 0;
  top: 50%;
  transform: translateY(-50%);
  height: 6px;
  border-radius: 999px;
  background: var(--ok);
  max-width: calc(100% - 10px);
}
.history td.pct { width: 1%; white-space: nowrap; color: var(--muted); font-variant-numeric: tabular-nums; }
.history td.change { width: 1%; white-space: nowrap; text-align: right; padding-right: 14px; }
/* The swing is not a label but a value, so it sheds the pill and reads as a
   plain coloured figure — a column of movement rather than prose. */
.history td.change .hl {
  margin: 0;
  padding: 0;
  background: none;
  border: none;
  border-radius: 0;
  font-size: .84rem;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  cursor: help;
}
.history td.change .hl-up   { color: var(--ok); }
.history td.change .hl-down { color: var(--fail); }
.history td.delta { color: var(--muted); }
.history tr.baseline td { color: var(--muted); }
.history .tag-base {
  margin-left: 8px;
  padding: 1px 6px;
  border-radius: 4px;
  background: #eef0f3;
  color: var(--muted);
  font-size: .62rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: .06em;
}
/* Why the row is here: passing count moved, coverage grew, or a build shipped. */
.history .hl {
  display: inline-block;
  margin: 1px 6px 1px 0;
  padding: 1px 7px;
  border-radius: 5px;
  font-size: .68rem;
  font-weight: 600;
  white-space: nowrap;
  background: #f1f2f4;
  border: 1px solid var(--line);
  color: var(--ink-2);
}
.history .hl-up      { background: rgba(26,168,97,.12);  border-color: rgba(26,168,97,.3);  color: #14764a; }
.history .hl-down    { background: rgba(212,69,44,.10);  border-color: rgba(212,69,44,.28); color: #a8402c; }
.history .hl-cover   { background: rgba(63,106,216,.10); border-color: rgba(63,106,216,.28); color: #2f4fa8; }
/* Coverage lost. Deliberately not red — nothing failed, the matrix shrank. */
.history .hl-drop    { background: #f1f2f4; border-color: var(--line-strong); color: var(--muted); text-decoration: line-through; text-decoration-thickness: 1px; }
.history .hl-version { background: rgba(214,138,23,.12); border-color: rgba(214,138,23,.3);  color: #8a5a0d; }
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
    <!-- Drives the summary/detailed switch. It sits here, ahead of every
         section, so the whole page can be styled from its :checked state with
         no script: a published report must work as a plain file. -->
    <input type="checkbox" id="view-detail" class="view-input"
           aria-label="Show detailed view" />
    <div class="header">
      {logo}
      <div class="titles">
        <h1>Wallet Interop Status</h1>
        <div class="run-ts">Run {run_ts}</div>
      </div>
      <label class="view-toggle" for="view-detail">
        <span class="vt-opt vt-summary">Summary</span>
        <span class="vt-track"><span class="vt-knob"></span></span>
        <span class="vt-opt vt-detail">Detailed</span>
      </label>
      <div class="totals">{pills}</div>
    </div>
    <div class="table-wrap">{sections}</div>
    {provenance}
    {flowline}
    {history}
    {info}
    <div class="footer">Generated {generated}</div>
  </div>
</body>
</html>
"""


def _outcome_td(cell: Optional[dict]) -> str:
    """One matrix cell: the outcome glyph, plus why it failed and whether it
    only passed on a retry.

    The caption under the glyph is the short reason name; the full sentence and
    the exception headline go in the tooltip, so the table stays scannable but
    nothing is lost for a reader who wants the detail.
    """
    if not cell or not cell.get("outcome"):
        return f'<td class="cell none" title="no test">{SVG_NONE}</td>'

    outcome = cell["outcome"]
    cls = OUTCOME_CLASS.get(outcome, "none")
    glyph = OUTCOME_SVG.get(outcome, SVG_NONE)
    title = [outcome]
    caption = ""

    reason = cell.get("reason")
    if reason:
        meta = FAILURE_CATEGORIES[reason]
        caption = f'<span class="reason">{html.escape(meta["short"])}</span>'
        title.append(f'{meta["label"]} — {meta["description"]}')
    if cell.get("detail"):
        title.append(cell["detail"])
    if cell.get("flaky"):
        cls += " flaky"
        caption = '<span class="reason">on retry</span>'
        earlier = cell["attempts"] - 1
        title.append(f'Passed on attempt {cell["attempts"]} — '
                     f'{earlier} earlier attempt{"s" if earlier != 1 else ""} failed.')

    change = cell.get("change")
    mark = ""
    if change in CHANGES:
        cls += f" changed change-{change}"
        note = CHANGES[change]["tooltip"]
        if change == "reason" and cell.get("previous_reason"):
            was = FAILURE_CATEGORIES.get(cell["previous_reason"], {})
            note = f'{note} (was: {was.get("label", cell["previous_reason"])})'
        title.append(note)
        mark = (f'<span class="chg chg-{change}" aria-hidden="true">'
                f'{CHANGES[change]["glyph"]}</span>')
    caption = mark + caption

    return (
        f'<td class="cell {cls}" title="{html.escape(chr(10).join(title), quote=True)}">'
        f'{glyph}{caption}</td>'
    )


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
    wallet_info = matrix.get("wallet_info", {})
    head_cells = ["<th class='row-head'></th>"]
    for col in columns:
        info = wallet_info.get(col["wallet"], {})
        version = version_label(info)
        note = " — no pytest report for this run" if col["no_report"] else ""
        cls = " class='no-data'" if col["no_report"] else ""
        # Everything the run knows about the build goes in the tooltip; only
        # the version is small enough to print in the header itself.
        tip = [col["label"] + note]
        if version:
            tip.append(f"Version {version}")
        if info.get("package"):
            tip.append(info["package"])
        device = _device_label(info)
        if device:
            tip.append(device)
        if info.get("updated"):
            tip.append(f"Updated during this run: "
                       f"{info.get('version_before')} \u2192 {info.get('version_after')}")
        elif info.get("update_available"):
            tip.append("A newer build was available and was not installed")
        ver_html = f"<span class='ver'>{html.escape(version)}</span>" if version else ""
        head_cells.append(
            f"<th{cls} title=\"{html.escape(chr(10).join(tip), quote=True)}\">"
            + "<div class='label'>"
            + icon_html(col["wallet"], "wallets", output_dir, embed)
            + f"<span>{html.escape(col['label'])}</span></div>{ver_html}</th>"
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


def render_info_section(info: Optional[dict], generated=()) -> str:
    """Build the explanatory cards under the matrix from report_info.json.

    The cards sit in two explicit columns rather than flowing across a grid,
    so a block can be placed deliberately: a "column" of "left" or "right" on
    a block puts it there, and the blocks within a column keep the order the
    file gives them. A block that says nothing alternates, which is what the
    grid did before columns existed.

    ``generated`` is an iterable of (column, html) pairs produced from the run
    itself — the reason key and the change key. They render even when there is
    no report_info.json to author the rest.
    """
    generated = [(col, markup) for col, markup in generated if markup]
    if not info and not generated:
        return ""
    info = info or {}
    columns = {"left": [], "right": []}
    auto = 0
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
        side = str(block.get("column", "")).strip().lower()
        if side not in columns:
            side = "left" if auto % 2 == 0 else "right"
            auto += 1
        columns[side].append(f'<div class="info-block">{"".join(inner)}</div>')
        blocks.append(side)
    for side, markup in generated:
        columns[side if side in columns else "right"].append(markup)
    filled = [side for side in ("left", "right") if columns[side]]
    if not filled:
        return ""
    # One empty column would render as a blank half-panel — with only the
    # generated keys to show (--no-info), the section becomes a single column.
    cls = "info-section" if len(filled) == 2 else "info-section one-col"
    if not blocks:
        # Only generated cards (--no-info): the whole section is detail-only.
        cls += " detail-only"
    cols = "".join(f'<div class="info-col">{"".join(columns[side])}</div>'
                   for side in filled)
    return f'<div class="{cls}">{cols}</div>'


def _device_label(info: dict) -> str:
    """"moto g24 / Android 14" — the hardware a wallet's results came from."""
    device = str(info.get("device_name") or "").strip()
    platform = str(info.get("platform") or "").strip()
    version = str(info.get("platform_version") or "").strip()
    os_part = f"{platform} {version}".strip()
    if device and os_part:
        return f"{device} / {os_part}"
    return device or os_part


def render_reason_key(matrix: dict) -> str:
    """A key for the reason captions, listing only what this run produced.

    Generated rather than authored: an entry can never describe a reason the
    matrix does not show, and a reason can never appear without an entry. The
    prose lives in FAILURE_CATEGORIES beside the patterns that assign it.
    """
    counts = matrix.get("reason_counts") or {}
    if not counts:
        return ""
    # FAILURE_CATEGORIES order, not count order: the reasons read as a
    # sequence from "never arrived" through to "arrived and was kept wrong".
    rows = []
    for key, meta in FAILURE_CATEGORIES.items():
        n = counts.get(key)
        if not n:
            continue
        rows.append(
            f'<li><span class="tag">{html.escape(meta["short"])}</span>'
            f'<span><strong>{html.escape(meta["label"])}</strong> — '
            f'{html.escape(meta["description"])} '
            f'<span class="count">{n} cell{"s" if n != 1 else ""}</span></span></li>'
        )
    if not rows:
        return ""
    return (
        '<div class="info-block generated">'
        '<div class="info-title">Why a cell is red</div>'
        '<p>Each failing cell carries a short reason under its mark. '
        'It names which side the flow broke down on.</p>'
        f'<ul class="reasons">{"".join(rows)}</ul></div>'
    )


def render_change_key(matrix: dict) -> str:
    """Explain the corner arrows, listing only the kinds this run produced.

    Generated from the same CHANGES table the cells use, for the same reason the
    reason key is generated: a key that can drift from the table is worse than
    no key.
    """
    counts = matrix.get("change_counts") or {}
    if not counts:
        return ""
    previous = matrix.get("compared_to")
    rows = []
    for key in ("broke", "fixed", "reason", "new"):
        n = counts.get(key)
        if not n:
            continue
        rows.append(
            f'<li><span class="chg-key chg chg-{key}">{CHANGES[key]["glyph"]}</span>'
            f'<span><strong>{html.escape(CHANGES[key]["label"])}</strong> — '
            f'{html.escape(CHANGES[key]["key"])} '
            f'<span class="count">{n} cell{"s" if n != 1 else ""}</span></span></li>'
        )
    if not rows:
        return ""
    since = (f' since the run of {html.escape(_run_date(previous))}'
             if previous else "")
    return (
        '<div class="info-block generated">'
        '<div class="info-title">What moved</div>'
        f'<p>An arrow in a cell\'s corner shows how it moved{since}: up if the '
        'pair started passing, down if it stopped. Cells without an arrow are '
        'unchanged.</p>'
        f'<ul class="reasons changes">{"".join(rows)}</ul></div>'
    )


def stage_breakdown(matrix: dict) -> dict:
    """How far each cell got, per suite, keyed by stage.

    Reads the same per-cell reasons the matrix shows, so the lines under the
    table are a second view of the table rather than a second measurement.
    """
    out = {}
    for suite, _ in SUITES:
        stages = {key: {"n": 0, "reasons": []} for key, _ in FLOW_STAGES}
        done = 0
        off_flow = 0
        for agents in matrix.get(suite, {}).values():
            for cell in agents.values():
                outcome = cell.get("outcome")
                if not outcome:
                    continue
                if outcome == "Passed":
                    done += 1
                    continue
                reason = cell.get("reason")
                stage = FAILURE_CATEGORIES.get(reason, {}).get("stage") if reason else None
                if stage not in stages:
                    # No reason, or a reason that is not part of the flow
                    # (a test-setup failure). Counted, but not placed.
                    off_flow += 1
                    continue
                stages[stage]["n"] += 1
                if reason not in stages[stage]["reasons"]:
                    stages[stage]["reasons"].append(reason)
        out[suite] = {"stages": stages, "done": done, "off_flow": off_flow}
    return out


def _flow_row(suite: str, title: str, data: dict) -> str:
    """One suite's walk through its own flow, left to right."""
    stages, done = data["stages"], data["done"]
    nodes = []
    for key, meta in FLOW_STAGES:
        if not stage_applies(meta, suite):
            continue
        label = stage_text(meta, "label", suite)
        counts = stages[key]
        n = counts["n"]
        reasons = ", ".join(FAILURE_CATEGORIES[r]["short"] for r in counts["reasons"])
        tip = [f'{label} — {stage_text(meta, "caption", suite)}.']
        tip.append(f'{n} {title.lower()} cell{"s" if n != 1 else ""} stopped here.'
                   if n else f'No {title.lower()} cell stopped here in this run.')
        count_html = (f'<span class="n">{n}</span><span class="unit">stopped</span>'
                      if n else '<span class="n none">&mdash;</span>')
        nodes.append(
            f'<li class="stage {"stopped" if n else "clear"}" '
            f'title="{html.escape(chr(10).join(tip), quote=True)}">'
            '<span class="dot"></span>'
            f'<span class="s-label">{html.escape(label)}</span>'
            f'<span class="s-count">{count_html}</span>'
            f'<span class="s-reasons">{html.escape(reasons)}</span>'
            "</li>"
        )
    tip = f'{done} {title.lower()} cell{"s" if done != 1 else ""} completed the flow.'
    nodes.append(
        f'<li class="stage done" title="{html.escape(tip, quote=True)}">'
        '<span class="dot"></span>'
        '<span class="s-label">Complete</span>'
        f'<span class="s-count"><span class="n">{done}</span>'
        '<span class="unit">passed</span></span>'
        '<span class="s-reasons"></span></li>'
    )
    return (f'<div class="flow-row"><div class="flow-suite">{html.escape(title)}</div>'
            f'<ol class="stages">{"".join(nodes)}</ol></div>')


def render_flow_line(matrix: dict) -> str:
    """One horizontal walk per suite, marking where wallets fell out.

    The matrix says which pairs failed; this says *where*. Reading left to
    right is reading the protocol in order, so a cluster on one node points at
    one shared problem — fifteen cells stopping at "link delivered" is a very
    different finding from fifteen spread along the line.

    Issuance and verification get a line each: they break at different points
    for different reasons, and averaging them hides which of the two is in
    trouble. Each line shows only the steps its own flow has, so the two are
    not the same length.
    """
    data = stage_breakdown(matrix)
    active = sum(d["done"] + d["off_flow"] + sum(v["n"] for v in d["stages"].values())
                 for d in data.values())
    if not active:
        return ""

    rows = "".join(_flow_row(suite, title, data[suite]) for suite, title in SUITES)

    note = ""
    off = {suite: data[suite]["off_flow"] for suite, _ in SUITES}
    total_off = sum(off.values())
    if total_off:
        split = ", ".join(f'{off[suite]} {title.lower()}' for suite, title in SUITES)
        # Its own class, not the quiet .flow-note the history strip uses: this
        # says part of the run is missing from the picture above it, and a
        # reader who skims past it has misread the lines.
        note = (f'<p class="unplaced-note"><strong>{total_off} cell'
                f'{"s are" if total_off != 1 else " is"} not on the lines above'
                f'</strong> — {split}. Their failure could not be tied to a step.</p>')
    return (
        '<div class="flowline detail-only">'
        '<div class="prov-title">Where the flow broke down</div>'
        f'{rows}{note}</div>'
    )


def render_provenance(matrix: dict) -> str:
    """What was actually under test: build, package and device per wallet.

    An interop result is only as meaningful as the build it was taken against,
    and the run already records this per wallet — it was simply never read.
    """
    info_by_wallet = matrix.get("wallet_info") or {}
    if not info_by_wallet:
        return ""
    seen = []
    for col in matrix.get("columns", []):
        if col["wallet"] not in seen:
            seen.append(col["wallet"])
    rows = []
    for wallet in seen:
        info = info_by_wallet.get(wallet)
        if not info:
            continue
        note = ""
        if info.get("updated"):
            note = (f'<span class="note upd">updated {html.escape(str(info.get("version_before")))}'
                    f' \u2192 {html.escape(str(info.get("version_after")))} during this run</span>')
        elif info.get("update_available"):
            note = '<span class="note">newer build available</span>'
        rows.append(
            "<tr>"
            f'<td class="w">{html.escape(display_name(wallet))}</td>'
            f'<td class="v">{html.escape(version_label(info)) or "&mdash;"}{note}</td>'
            f'<td class="p">{html.escape(str(info.get("package") or ""))}</td>'
            f'<td class="d">{html.escape(_device_label(info))}</td>'
            "</tr>"
        )
    if not rows:
        return ""
    return (
        '<div class="provenance detail-only"><div class="prov-title">Builds under test</div>'
        "<table><thead><tr><th>Wallet</th>"
        '<th title="The app\'s versionName, with its Android versionCode in '
        'brackets. The code is the build identity Android itself compares.">'
        "Version (build)</th><th>Package</th>"
        f"<th>Device</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div>"
    )


def _run_date(run_ts: str) -> str:
    """Render a run directory name as a readable date, or pass it through."""
    try:
        return datetime.strptime(run_ts, "%Y-%m-%d_%H-%M-%S").strftime("%d %b %Y, %H:%M")
    except (ValueError, TypeError):
        return str(run_ts)


def _coverage(entry: dict):
    """The column keys and agent names a recorded run covered."""
    columns, agents = set(), set()
    for rows in (entry.get("cells") or {}).values():
        for col, ags in rows.items():
            columns.add(col)
            agents.update(ags)
    return columns, agents


def _column_name(key: str) -> str:
    """Readable name for a stored column key ("gataca-jwk" -> "Gataca (jwk)").

    Wallet directory names carry no hyphen, so the first segment is the
    wallet and anything after it is the parametrize variant.
    """
    wallet, _, variant = key.partition("-")
    return column_label(wallet, variant or None)


def _version_changes(entry: dict, earlier: Optional[dict]) -> list:
    """Wallets whose build differs from the run before it.

    This is what makes the history worth keeping rather than a row of tallies:
    a column that turned red between two runs usually turned red because
    something shipped.
    """
    if not earlier:
        return []
    notes = []
    before = earlier.get("wallets") or {}
    for wallet, info in sorted((entry.get("wallets") or {}).items()):
        was = before.get(wallet)
        if not was:
            continue
        was_code, code = str(was.get("version_code")), str(info.get("version_code"))
        if was_code == code:
            continue
        was_name, name = was.get("version_name"), info.get("version_name")
        if was_name and name and was_name != name:
            change = f'{was_name} \u2192 {name}'
        elif name:
            # Same marketing version, new build — hovi shipped 29 -> 34 as
            # 1.3.0 both times. Showing "1.3.0 -> 1.3.0" would read as a bug.
            change = f'{name} ({was_code} \u2192 {code})'
        else:
            change = f'build {was_code} \u2192 {code}'
        notes.append(f'{display_name(wallet)} {change}')
    return notes


def run_highlights(entry: dict, earlier: Optional[dict]) -> list:
    """Why this run earns a row in the strip, as (kind, text) pairs.

    A run that matched the one before it on every count says nothing a reader
    needs; listing it only makes the rows that do matter harder to find. Four
    things qualify: the number of passing cells moved, the run covered ground
    the previous one did not, it stopped covering ground the previous one did,
    or a wallet shipped a new build.
    """
    if not earlier:
        return []
    out = []

    passed = (entry.get("totals") or {}).get("Passed", 0)
    was_passed = (earlier.get("totals") or {}).get("Passed", 0)
    if passed != was_passed:
        delta = passed - was_passed
        kind = "up" if delta > 0 else "down"
        arrow = "\u2191" if delta > 0 else "\u2193"
        word = "more" if delta > 0 else "fewer"
        out.append((kind, f"{arrow}{abs(delta)}",
                    f"{abs(delta)} {word} cell{'s' if abs(delta) != 1 else ''} "
                    f"passing than the previous recorded run."))

    columns, agents = _coverage(entry)
    was_columns, was_agents = _coverage(earlier)
    for key in sorted(columns - was_columns):
        out.append(("cover", f"new wallet: {_column_name(key)}", ""))
    for agent in sorted(agents - was_agents):
        out.append(("cover", f"new agent: {display_name(agent)}", ""))
    # Coverage lost matters as much as coverage gained, and is easier to miss:
    # a wallet that stops being tested leaves no red cell behind, it simply
    # stops having cells. Without this the passing count can fall and nothing
    # on the page says the matrix got smaller.
    for key in sorted(was_columns - columns):
        out.append(("drop", f"dropped wallet: {_column_name(key)}", ""))
    for agent in sorted(was_agents - agents):
        out.append(("drop", f"dropped agent: {display_name(agent)}", ""))

    for note in _version_changes(entry, earlier):
        out.append(("version", note, ""))
    return out


def _highlights_html(highlights: list) -> str:
    """Render (kind, text, tip) tags. The tip carries what the text omits —
    the swing tag is two characters, so its meaning lives in the tooltip."""
    if not highlights:
        return ""
    out = []
    for kind, text, tip in highlights:
        attr = f' title="{html.escape(tip, quote=True)}"' if tip else ""
        out.append(f'<span class="hl hl-{kind}"{attr}>{html.escape(text)}</span>')
    return "".join(out)


def _history_rows(history: list, current_ts: str):
    """Which recorded runs earn a row, newest first, and how many do not.

    Shared by the strip and by the key that explains it, so the two can never
    describe different sets of runs.
    """
    # The page is a snapshot of one run, so the history stops there: a report
    # generated for an older run must not list runs that came after it.
    history = [r for r in history if str(r.get("run_ts")) <= str(current_ts)]
    if len(history) < 2:
        return [], 0
    rows, hidden = [], 0
    for idx in range(len(history) - 1, -1, -1):
        entry = history[idx]
        earlier = history[idx - 1] if idx > 0 else None
        highlights = run_highlights(entry, earlier)
        is_current = str(entry.get("run_ts")) == str(current_ts)
        # The first recorded run is the baseline every later row is measured
        # against, and the current run is what the page is about; neither is
        # dropped for being unchanged, though the row cap below can still cut
        # the baseline when there are more interesting runs than fit.
        if (not highlights and not is_current and earlier is not None) \
                or len(rows) >= HISTORY_SHOWN:
            hidden += 1
            continue
        rows.append({"entry": entry, "earlier": earlier,
                     "highlights": highlights, "current": is_current})
    return rows, hidden


def render_history(history: list, current_ts: str) -> str:
    """A compact strip of the runs that changed something, newest first.

    Deliberately terse, and deliberately incomplete: runs that repeated the
    previous result exactly are counted at the foot rather than listed. The
    full record stays in the history file for anyone who needs it.
    """
    rows, hidden = _history_rows(history, current_ts)
    if not rows:
        return ""
    out = []
    for row in rows:
        entry, highlights, is_current = row["entry"], row["highlights"], row["current"]
        swing = [h for h in highlights if h[0] in ("up", "down")]
        updates = [h for h in highlights if h[0] not in ("up", "down")]
        totals = entry.get("totals") or {}
        passed = totals.get("Passed", 0)
        failed = totals.get("Failed", 0) + totals.get("Error", 0)
        total = passed + failed
        pct = round(100 * passed / total) if total else 0
        row_cls = " ".join(c for c in ("current" if is_current else "",
                                       "baseline" if row["earlier"] is None else "") if c)
        tag = '<span class="tag-now">this run</span>' if is_current else (
            '<span class="tag-base">baseline</span>' if row["earlier"] is None else "")
        out.append(
            f'<tr class="{row_cls}">'
            f'<td class="when">{html.escape(_run_date(entry.get("run_ts", "")))}{tag}</td>'
            f'<td class="tally"><span class="ok">{passed}</span>'
            f'<span class="sep">/</span><span class="fail">{failed}</span></td>'
            f'<td class="bar"><span class="fill" style="width:{pct}%"></span></td>'
            f'<td class="pct">{pct}%</td>'
            # The passing delta gets a column of its own, ahead of the list:
            # it is the thing a reader scans the strip for, and buried among
            # three build changes it stops being findable.
            f'<td class="change">{_highlights_html(swing)}</td>'
            f'<td class="delta">{_highlights_html(updates)}</td>'
            "</tr>"
        )
    note = ""
    if hidden:
        note = (f'<p class="flow-note">{hidden} further recorded run'
                f'{"s" if hidden != 1 else ""} not shown — same passing count, '
                'same coverage, same wallet builds as the run before.</p>')
    return (
        '<div class="history detail-only"><div class="prov-title">History overview</div>'
        f'<table><tbody>{"".join(out)}</tbody></table>{note}</div>'
    )


def render_history_key(history: list, current_ts: str) -> str:
    """Explain the run strip, using the strip's own markup for the samples.

    Generated like the other keys: the swatches are the same spans the table
    renders, so a style or wording change cannot leave the key behind.
    """
    rows, hidden = _history_rows(history, current_ts)
    if not rows:
        return ""
    kept = len([r for r in history if str(r.get("run_ts")) <= str(current_ts)])
    samples = [
        (_highlights_html([("up", "\u21912", "")]),
         "Passing cells against the previous recorded run. Green is a gain, "
         "red a loss; hover it for the wording."),
        (_highlights_html([("cover", "new agent: walt.id Issuer", "")]),
         "The run covered a wallet or an agent the one before it did not, so "
         "the totals are not measuring the same matrix."),
        (_highlights_html([("drop", "dropped wallet: Gataca (ebsi)", "")]),
         "The run stopped covering something the one before it tested. No "
         "cell turns red for this — the matrix simply gets smaller, which "
         "moves the totals on its own."),
        (_highlights_html([("version", "Procivis 1.85.2 \u2192 1.85.3", "")]),
         "A wallet shipped a new build between the two runs — usually the "
         "reason a column changed colour. A new build under an unchanged "
         "version name shows its build number instead."),
    ]
    items = "".join(f'<li><span class="hkey-sample">{sample}</span>'
                    f'<span>{html.escape(text)}</span></li>'
                    for sample, text in samples)
    tail = (f' {hidden} of the {kept} recorded runs repeated the previous result '
            'exactly and are counted rather than listed.') if hidden else ""
    return (
        '<div class="info-block generated">'
        '<div class="info-title">Reading the run history</div>'
        '<p>One row per past run, newest first, and only for runs that changed '
        'something: the number of passing cells moved, the run covered new '
        f'ground, or a wallet shipped.{html.escape(tail)}</p>'
        f'<ul class="reasons hkey">{items}</ul></div>'
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


def _pills_html(totals: dict, flaky_cells: int = 0) -> str:
    parts = [
        ("ok",   SVG_PASS, totals.get("Passed", 0),  "passing"),
        ("fail", SVG_FAIL, totals.get("Failed", 0),  "failing"),
    ]
    if totals.get("Error"):
        parts.append(("err",  SVG_ERR,  totals["Error"],   "errored"))
    if totals.get("Skipped"):
        parts.append(("skip", SVG_SKIP, totals["Skipped"], "skipped"))
    if flaky_cells:
        parts.append(("flaky", SVG_PASS, flaky_cells, "on retry"))
    return "".join(
        f'<span class="pill {cls}">{svg}<span>{n} {label}</span></span>'
        for cls, svg, n, label in parts
    )


def render_html(matrix: dict, output_dir: Path, embed: bool,
                info: Optional[dict] = None, history: Optional[list] = None) -> str:
    sections = render_combined_table(matrix, output_dir, embed)
    # The generated reason key joins the authored blocks in the same grid, so
    # it reads as one section rather than a bolted-on extra.
    return PAGE.format(
        run_ts=html.escape(matrix["run_ts"]),
        css=CSS,
        logo=_logo_html(output_dir, embed),
        pills=_pills_html(matrix["totals"], matrix.get("flaky_cells", 0)),
        sections=sections,
        provenance=render_provenance(matrix),
        flowline=render_flow_line(matrix),
        history=render_history(history or [], matrix["run_ts"]),
        info=render_info_section(info, generated=(
            # What changed comes before why a cell is red: a returning reader
            # wants the movement since last time first, the full vocabulary
            # second.
            ("right", render_change_key(matrix)),
            ("right", render_reason_key(matrix)),
            # The run-history key balances the deck from the left column: the
            # right one already carries the two keys about the matrix itself.
            ("left", render_history_key(history or [], matrix["run_ts"])),
        )),
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
    ap.add_argument("--history", default=None,
                    help=f"Run history JSON (default: <output>/{HISTORY_FILENAME})")
    ap.add_argument("--no-history", action="store_true",
                    help="Do not read or record run history for this report")
    ap.add_argument("--backfill", action="store_true",
                    help="Scan for past runs and record every one of them in the "
                         "history before rendering (default scan: the run's own "
                         "reports/ directory)")
    ap.add_argument("--scan", action="append", metavar="DIR",
                    help="Directory of run dirs to scan with --backfill. Repeatable; "
                         "overrides the default")
    ap.add_argument("--min-cells", type=int, default=DEFAULT_MIN_CELLS, metavar="N",
                    help=f"With --backfill, skip runs charting fewer than N cells "
                         f"(default {DEFAULT_MIN_CELLS}); keeps partial and "
                         f"single-wallet runs out of the history")
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

    # History follows --output for the same reason the info file does. It is
    # read before rendering (the page marks what changed) and written after
    # (this run joins the record).
    history_path = Path(args.history) if args.history else output_dir / HISTORY_FILENAME
    history = [] if args.no_history else load_history(history_path)

    if args.backfill:
        if args.no_history:
            sys.exit("--backfill and --no-history contradict each other")
        roots = [Path(d) for d in args.scan] if args.scan else [run_dir.parent]
        kept, skipped = discover_runs(roots, args.min_cells)
        history = backfill_history(history, kept)
        print(f"backfilled {len(kept)} run{'s' if len(kept) != 1 else ''} from "
              f"{', '.join(str(r) for r in roots)}")
        if skipped:
            print(f"  skipped {len(skipped)} charting fewer than "
                  f"{args.min_cells} cells: "
                  + ", ".join(f"{n} ({c})" for n, _p, _m, c in skipped[:6])
                  + (" ..." if len(skipped) > 6 else ""))

    # Backfill first, so the page compares against the run that really came
    # before it rather than against whatever happened to be recorded already.
    annotate_changes(matrix, previous_run(history, matrix["run_ts"]))
    if not args.no_history:
        history = update_history(history, history_entry(matrix))

    page = render_html(matrix, output_dir, args.embed_icons, info, history)
    (output_dir / "index.html").write_text(page, encoding="utf-8")
    (output_dir / "data.json").write_text(json.dumps(matrix, indent=2), encoding="utf-8")
    print(f"wrote {output_dir / 'index.html'}")
    print(f"wrote {output_dir / 'data.json'}")
    if not args.no_history:
        write_history(history_path, history)
        print(f"wrote {history_path} ({len(history)} run"
              f"{'s' if len(history) != 1 else ''} kept)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
