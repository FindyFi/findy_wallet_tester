"""Framework failures are labelled cells, but they are not interop findings.

A lost device session or a failed test setup says nothing about the wallet, so the header totals
and the history pass rate leave them out. The cell itself still renders, with its reason.
"""
import html
import json

import pytest

import generate_compact_report as report

NODE = ("wallets/hovi/tests/test_credential_issuance.py::test_credential_issuance["
        "{agent}/credential_issuance-hovi]")
SETUP = "AssertionError: Active DID is '', expected 'JWK Identity' for did_method 'jwk'"
LOST = "RuntimeError: something about the session"


def _tid(agent):
    return NODE.format(agent=agent)


def _run(tmp_path, tests, categories=None):
    """tests: {agent: (result, headline)}; categories: {agent: category} written to summary.json."""
    wallet = tmp_path / "run" / "hovi"
    wallet.mkdir(parents=True)
    data = {_tid(a): [{"result": res, "log": f"E   {head}\n"}] for a, (res, head) in tests.items()}
    blob = html.escape(json.dumps({"tests": data}), quote=True)
    (wallet / "report.html").write_text(f'<div id="data-container" data-jsonblob="{blob}"></div>')
    if categories:
        rows = [{"nodeid": _tid(a), "outcome": "failed", "category": c}
                for a, c in categories.items()]
        (wallet / "summary.json").write_text(json.dumps({"tests": rows}))
    return wallet.parent


def test_each_fixture_id_parses():
    for agent in ("a_issuer", "b_issuer"):
        assert report.parse_test_id(_tid(agent))


def test_a_lost_session_is_counted_as_framework_not_failed(tmp_path):
    run = _run(tmp_path,
               {"a_issuer": ("Failed", LOST), "b_issuer": ("Failed", "RuntimeError: nope"),
                "c_issuer": ("Passed", "")},
               {"a_issuer": "lost_session", "b_issuer": "rejected"})
    totals = report.collect(run)["totals"]
    assert (totals["Framework"], totals["Failed"], totals["Passed"]) == (1, 1, 1)


def test_a_test_setup_failure_is_counted_as_framework_too(tmp_path):
    totals = report.collect(_run(tmp_path, {"a_issuer": ("Failed", SETUP)}))["totals"]
    assert (totals["Framework"], totals["Failed"]) == (1, 0)


def test_an_errored_lost_session_is_counted_as_framework_not_error(tmp_path):
    run = _run(tmp_path, {"a_issuer": ("Error", LOST)}, {"a_issuer": "lost_session"})
    totals = report.collect(run)["totals"]
    assert (totals["Framework"], totals["Error"]) == (1, 0)


def test_the_cell_is_still_shown_and_labelled(tmp_path):
    run = _run(tmp_path, {"a_issuer": ("Failed", LOST)}, {"a_issuer": "lost_session"})
    cell = report.collect(run)["issuance"]["hovi"]["a_issuer"]
    assert cell["reason"] == "lost_session"
    assert cell["outcome"] == "Failed"


@pytest.mark.parametrize("lost_first", [True, False])
def test_each_test_case_is_counted_by_its_own_reason_not_by_its_merged_cell(tmp_path, lost_first):
    """Two cases share one cell; the cell shows the higher-ranked rejection, but the lost session
    beside it is still the framework's, and the totals count test cases, not cells. Both read
    orders, because the merged cell keeps the rejection either way."""
    wallet = tmp_path / "run" / "hovi"
    wallet.mkdir(parents=True)
    ids = [NODE.format(agent="a_issuer"),
           NODE.format(agent="a_issuer").replace("credential_issuance-", "pension_issuance-")]
    rows = [{"nodeid": ids[0], "outcome": "failed", "category": "lost_session"},
            {"nodeid": ids[1], "outcome": "failed", "category": "rejected"}]
    if not lost_first:
        rows.reverse()
    data = {row["nodeid"]: [{"result": "Failed", "log": f"E   {LOST}\n"}] for row in rows}
    blob = html.escape(json.dumps({"tests": data}), quote=True)
    (wallet / "report.html").write_text(f'<div id="data-container" data-jsonblob="{blob}"></div>')
    (wallet / "summary.json").write_text(json.dumps({"tests": rows}))
    matrix = report.collect(wallet.parent)
    assert matrix["issuance"]["hovi"]["a_issuer"]["reason"] == "rejected"
    assert (matrix["totals"]["Framework"], matrix["totals"]["Failed"]) == (1, 1)


def test_a_failure_with_no_reason_still_counts_as_failed(tmp_path):
    run = _run(tmp_path, {"a_issuer": ("Failed", "RuntimeError: something odd")})
    totals = report.collect(run)["totals"]
    assert (totals["Failed"], totals["Framework"]) == (1, 0)


def test_the_history_pass_rate_leaves_framework_failures_out():
    now = {"run_ts": "2026-11-02_10-00-00", "totals": {"Passed": 1, "Failed": 1, "Framework": 2}}
    old = {"run_ts": "2026-11-01_10-00-00", "totals": {"Passed": 1, "Failed": 3}}
    out = report.render_history([old, now], now["run_ts"])
    assert "50%" in out
    assert "25%" in out  # the old entry, which has no Framework key, still renders


def test_the_header_pill_names_the_uncounted_failures():
    out = report._pills_html({"Passed": 1, "Failed": 1, "Error": 0, "Skipped": 0, "Framework": 2})
    assert "2 not counted" in out
    assert 'title="Failures of the test framework itself' in out
    for totals in ({"Passed": 1, "Failed": 1, "Framework": 0}, {"Passed": 1, "Failed": 1}):
        out = report._pills_html(totals)
        assert "not counted" not in out and "title=" not in out


def test_the_other_pills_are_unchanged():
    """Compared against the markup the code produced before the framework pill existed."""
    expected = (
        '<span class="pill ok">' + report.SVG_PASS + '<span>1 passing</span></span>'
        '<span class="pill fail">' + report.SVG_FAIL + '<span>1 failing</span></span>'
        '<span class="pill err">' + report.SVG_ERR + '<span>2 errored</span></span>'
        '<span class="pill skip">' + report.SVG_SKIP + '<span>3 skipped</span></span>'
        '<span class="pill flaky">' + report.SVG_PASS + '<span>4 on retry</span></span>'
    )
    assert report._pills_html({"Passed": 1, "Failed": 1, "Error": 2, "Skipped": 3}, 4) == expected


def test_both_framework_descriptions_say_they_are_not_counted():
    for key in ("lost_session", "setup"):
        assert report.FAILURE_CATEGORIES[key]["description"].endswith(
            "It is not counted in the pass and fail totals.")
