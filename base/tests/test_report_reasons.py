"""The published matrix takes a red cell's reason from what the flow asserted, not from its prose.

`classify_failure` reads a category back out of the exception's text, which made wording a
published contract. summary.json already holds the category as an attribute — the root conftest
reads `FlowFailure.category` off the exception — so the report prefers it and keeps the prose
reading only for runs that have no summary.json.

Three things are pinned here:

  * every outcome `base/outcome.py` can raise has a label in the report. Three did not
    (`crashed`, `prompt_loop`, `lost_session`), so a correctly typed failure rendered as a plain
    red cell;
  * a typed category beats the prose, and runs without summary.json render as before;
  * the test-decided outcomes (`not_stored`, `nothing_to_present`) carry `.category` too, where a
    bare `assert` or `pytest.fail` carried it only as text.
"""
import html
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import generate_compact_report as report
from base import credential_count, outcome

ROOT = Path(__file__).parents[2]
TID = ("wallets/hovi/tests/test_credential_issuance.py::test_credential_issuance["
       "hovi_issuer/credential_issuance-hovi]")


def _outcome_names():
    """Every outcome name `base/outcome.py` defines, except the one that is not a failure."""
    return sorted(v for k, v in vars(outcome).items()
                  if k.isupper() and not k.startswith("_") and isinstance(v, str)
                  and v != outcome.SUCCESS)


def _failed(headline: str) -> list:
    """One pytest-html attempt record that failed with `headline`."""
    return [{"result": "Failed", "log": f"E   {headline}\n"}]


# --- every outcome renders -----------------------------------------------------------------------

@pytest.mark.parametrize("name", _outcome_names())
def test_every_outcome_the_harness_can_raise_has_a_label(name):
    """The report only honours categories it lists; an unlisted one renders as a plain red cell."""
    assert name in report.FAILURE_CATEGORIES, (
        f"base/outcome.py can raise [{name}] but the report has no entry for it, so the cell "
        "would carry no reason at all"
    )
    assert name in report.CATEGORY_RANK, f"[{name}] has no precedence when cells merge"


@pytest.mark.parametrize("name", ["crashed", "prompt_loop", "lost_session"])
def test_a_typed_failure_in_the_log_is_read_as_its_category(name):
    """The three that used to fall through: typed correctly, published as nothing."""
    reason, _ = report.classify_failure(
        f"E   base.outcome.FlowFailure: [{name}] [credential_flow] something happened\n")
    assert reason == name


def test_a_lost_session_is_never_ranked_above_a_wallet_finding():
    """Our own breakage must not outrank a sibling case's real interop reason in a merged cell."""
    wallet_findings = [c for c in report.FAILURE_CATEGORIES if c not in ("lost_session", "harness")]
    assert all(report.CATEGORY_RANK["lost_session"] < report.CATEGORY_RANK[c]
               for c in wallet_findings)
    assert report.FAILURE_CATEGORIES["lost_session"]["stage"] is None


def test_no_two_categories_tie_in_rank():
    """`merge_cells` keeps the existing cell on a tie, so test order would decide what is shown —
    the order-dependence the rank exists to prevent."""
    ranks = [report.CATEGORY_RANK[c] for c in report.FAILURE_CATEGORIES]
    assert len(ranks) == len(set(ranks))


def test_a_crash_outranks_a_rejection_in_a_merged_cell():
    """A crash is the more definitive finding about the wallet: it could not even say no."""
    rejected = report.make_cell(_failed("base.outcome.FlowFailure: [rejected] x"))
    crashed = report.make_cell(_failed("base.outcome.FlowFailure: [crashed] x"))
    assert report.merge_cells(rejected, crashed)["reason"] == "crashed"
    assert report.merge_cells(crashed, rejected)["reason"] == "crashed"


# --- summary.json wins, the log is the fallback ----------------------------------------------------

def test_a_typed_category_beats_what_the_prose_would_say():
    """authbound's sentence says *no* error screen appeared, and the regex reads it as `rejected`."""
    recs = _failed("RuntimeError: [credential_flow] 'x' was accepted and authenticated, but "
                   "neither the success nor the error screen appeared within 30s")
    assert report.make_cell(recs)["reason"] == "rejected", "the misfile this guards against"

    said = "FlowFailure: [processing] [credential_flow] 'x' was accepted, then nothing for 30s"
    cell = report.make_cell(recs, {"category": "processing", "error": said})
    assert cell["reason"] == "processing"
    assert cell["detail"] == said, "a disagreeing log line must not be published under the reason"


def test_when_prose_and_record_agree_the_log_headline_is_the_detail():
    headline = "base.outcome.FlowFailure: [rejected] [credential_flow] hovi said no"
    cell = report.make_cell(_failed(headline), {"category": "rejected", "error": "short form"})
    assert cell["detail"] == headline


def test_a_teardown_error_after_a_typed_failure_does_not_become_its_detail():
    """pytest-html gives a failed teardown its own record, so the last one can be the teardown's,
    while summary.json rightly keeps the call's verdict. The tooltip would read "Rejected" beside a
    Selenium session error."""
    call = "base.outcome.FlowFailure: [rejected] [credential_flow] hovi refused the offer"
    recs = [{"result": "Failed", "log": f"E   {call}\n"},
            {"result": "Error", "log": "E   selenium.common.exceptions.InvalidSessionIdException: "
                                       "Message: A session is either terminated or not started\n"}]
    cell = report.make_cell(recs, {"category": "rejected", "error": call})
    assert cell["reason"] == "rejected"
    assert "InvalidSessionId" not in cell["detail"]
    assert cell["detail"] == call


def test_an_untyped_record_falls_back_to_the_prose():
    recs = _failed("RuntimeError: Toppan error dialog: Unsupported client_id prefix")
    assert report.make_cell(recs, {"category": ""})["reason"] == "rejected"


def test_a_category_the_report_does_not_know_falls_back_rather_than_publishing_it():
    recs = _failed("RuntimeError: Toppan error dialog: Unsupported client_id prefix")
    assert report.make_cell(recs, {"category": "not_a_category"})["reason"] == "rejected"


def test_a_passing_cell_ignores_a_stale_category():
    cell = report.make_cell([{"result": "Passed", "log": ""}], {"category": "rejected"})
    assert cell["reason"] is None


def test_the_detail_comes_from_the_record_when_the_log_has_none():
    cell = report.make_cell([{"result": "Failed", "log": ""}],
                            {"category": "absent", "error": "FlowFailure: [absent] nothing came"})
    assert cell["reason"] == "absent"
    assert cell["detail"] == "FlowFailure: [absent] nothing came"


def _run_dir(tmp_path, headline, summary=None):
    """A one-wallet run directory holding a pytest-html report and, optionally, summary.json."""
    wallet = tmp_path / "run" / "hovi"
    wallet.mkdir(parents=True)
    blob = html.escape(json.dumps({"tests": {TID: _failed(headline)}}), quote=True)
    (wallet / "report.html").write_text(f'<div id="data-container" data-jsonblob="{blob}"></div>')
    if summary is not None:
        (wallet / "summary.json").write_text(summary)
    return wallet.parent


def _reason(run_dir):
    return report.collect(run_dir)["issuance"]["hovi"]["hovi_issuer"]["reason"]


PROSE = "RuntimeError: [credential_flow] Credential issuance failed for 'x': boom"


def test_a_run_with_summary_json_is_read_from_it(tmp_path):
    summary = json.dumps({"tests": [{"nodeid": TID, "outcome": "failed", "category": "absent"}]})
    assert _reason(_run_dir(tmp_path, PROSE, summary)) == "absent"


def test_a_run_without_summary_json_renders_as_before(tmp_path):
    """Archived runs and `--backfill` predate summary.json, and xdist runs never write it."""
    assert _reason(_run_dir(tmp_path, PROSE)) == "rejected"


@pytest.mark.parametrize("damaged", [
    "{ not json",
    '{"tests": null}',
    '{"tests": 5}',
    '["not", "a", "dict"]',
    '{"tests": [{"nodeid": 7, "category": "absent"}]}',
])
def test_a_damaged_summary_json_costs_its_typed_reasons_not_the_report(tmp_path, damaged):
    """One wallet's file must never take the nightly publish down with it."""
    assert _reason(_run_dir(tmp_path, PROSE, damaged)) == "rejected"


def test_a_summary_json_that_is_not_utf8_is_ignored(tmp_path):
    run = _run_dir(tmp_path, PROSE)
    (run / "hovi" / "summary.json").write_bytes(b'{"tests": [\xff\xfe]}')
    assert _reason(run) == "rejected"
    assert report.load_summary(tmp_path / "nowhere") == {}


def test_a_category_that_is_not_a_string_falls_back_to_the_prose():
    recs = _failed("RuntimeError: Toppan error dialog: Unsupported client_id prefix")
    assert report.make_cell(recs, {"category": ["rejected"]})["reason"] == "rejected"
    assert report.make_cell(recs, {"category": None})["reason"] == "rejected"


# --- test-decided outcomes carry the category ---------------------------------------------------

def _request():
    return SimpleNamespace(node=SimpleNamespace(user_properties=[]))


def test_a_flat_count_fails_as_not_stored_with_the_category_attached():
    with pytest.raises(outcome.FlowFailure) as exc:
        credential_count.assert_increased(2, 2, request=_request(), wallet="hovi",
                                          issuer="hovi_issuer", case="credential_issuance")
    assert exc.value.category == outcome.NOT_STORED
    assert str(exc.value).startswith("[not_stored] Credential 'credential_issuance' from "
                                     "'hovi_issuer' did not reach hovi")


def test_a_count_that_moved_passes():
    credential_count.assert_increased(1, 2, request=_request(), wallet="hovi",
                                      issuer="hovi_issuer", case="credential_issuance")


def test_an_unreadable_count_still_passes_with_the_absence_recorded():
    """Unchanged: no count is no evidence either way, and the test says so rather than failing."""
    request = _request()
    credential_count.assert_increased(None, 2, request=request, wallet="hovi",
                                      issuer="hovi_issuer", case="credential_issuance")
    assert ("count_evidence", "unavailable") in request.node.user_properties


def test_outcome_fail_raises_the_named_outcome():
    with pytest.raises(outcome.FlowFailure) as exc:
        outcome.fail(outcome.NOTHING_TO_PRESENT, "the wallet holds no credentials")
    assert exc.value.category == outcome.NOTHING_TO_PRESENT
    assert str(exc.value) == "[nothing_to_present] the wallet holds no credentials"


def test_a_not_stored_failure_reaches_the_record_the_report_reads(tmp_path):
    """Through the real root conftest, as `test_error_screen_hook.py` does for flow failures: the
    attribute has to survive to `_failure_category`, which is what summary.json is built from."""
    out = tmp_path / "hook.json"
    case = Path(__file__).parent / "hookcheck" / "not_stored_case.py"
    result = subprocess.run(
        [sys.executable, "-m", "pytest", str(case), "-q", "--reruns", "0", "-p", "no:cacheprovider"],
        cwd=ROOT, env={**os.environ, "HOOKCHECK_OUT": str(out), "TMPDIR": str(tmp_path)},
        capture_output=True, text=True, timeout=180,
    )
    assert out.exists(), f"the child session never reached teardown:\n{result.stdout[-3000:]}"
    left = json.loads(out.read_text())
    assert left["category"] == "not_stored", (
        "the category did not reach the node — a bare assert used to leave it empty, so "
        "summary.json and app.log recorded every not_stored as uncategorised"
    )
