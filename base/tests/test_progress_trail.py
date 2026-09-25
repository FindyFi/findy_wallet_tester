"""A failure records how far its flow got, and the report places the cell there.

An outcome category says why a flow stopped, never where. Twenty-four of the forty-one red cells in
the 2026-09-21 run read "rejected", spanning paradym failing straight after the deeplink and
paradym failing after the PIN. The report drew every one of them at "accept", because the stage
came from the category — an inference, not a measurement.

`base/progress.py` records each stage a flow *completes*. Pinned here:

  * the record itself: furthest stage, ordered steps, and a step that does not advance the stage;
  * the shared code writes it, so a wallet on the shared machinery needs no lines of its own:
    `deeplink.fire`, `outcome.wait_for`, `outcome.raise_if_rejected`, `assert_increased`;
  * **never later than the truth** — a step whose success could not be checked does not claim its
    stage;
  * it reaches summary.json, app.log and the published cell, through the real root conftest, and
    starts empty on every attempt.
"""
import html
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from selenium.common.exceptions import WebDriverException

import generate_compact_report as report
from base import credential_count, deeplink, outcome, progress
from base.conftest_helpers import record_error_screen
from base.flow_context import FlowContext
from base.interstitials import Interstitial
from base.screens import Screens

ROOT = Path(__file__).parents[2]


@pytest.fixture(autouse=True)
def fresh_trail(monkeypatch):
    progress.reset()
    monkeypatch.setattr(outcome, "detect_crash_or_anr", lambda driver, timeout=0.3: None)
    yield
    progress.reset()


def _stages():
    return [s.split(":")[0] for s in progress.snapshot()["steps"]]


# --- the record ----------------------------------------------------------------------------------

def test_the_furthest_stage_completed_is_the_one_reported():
    progress.reached(progress.DELIVER, "deeplink delivered")
    progress.reached(progress.PRESENT, "offer shown")
    snap = progress.snapshot()
    assert snap["stage"] == progress.PRESENT
    assert snap["steps"] == ["deliver: deeplink delivered", "present: offer shown"]


def test_a_step_is_recorded_without_advancing_the_stage():
    progress.reached(progress.DELIVER, "deeplink delivered")
    progress.step("answered device-pin (1)")
    snap = progress.snapshot()
    assert snap["stage"] == progress.DELIVER
    assert snap["steps"][-1] == "deliver: answered device-pin (1)"


def test_a_step_before_any_stage_is_placed_at_the_start():
    progress.step("tapped something")
    assert progress.snapshot() == {"stage": "", "steps": ["start: tapped something"]}


def test_an_unknown_stage_is_ignored_rather_than_published():
    progress.reached("almost_done", "made up")
    assert progress.snapshot() == {"stage": "", "steps": []}


def test_the_furthest_stage_wins_over_the_latest():
    """A later wait for an earlier-looking screen must not move the record backwards."""
    progress.reached(progress.PRESENT, "offer shown")
    progress.reached(progress.DELIVER, "a second deeplink")
    assert progress.snapshot()["stage"] == progress.PRESENT


def test_reset_empties_it():
    progress.reached(progress.KEEP, "stored")
    progress.reset()
    assert progress.snapshot() == {"stage": "", "steps": []}


def test_the_stages_are_the_reports_own_flow_diagram():
    """One list, read by both sides. A stage the report cannot label would vanish from it."""
    assert [key for key, _ in report.FLOW_STAGES] == list(progress.STAGES)


# --- written by the shared code ------------------------------------------------------------------

class FiringDriver:
    current_package = "com.example.wallet"
    capabilities: dict = {}

    def __init__(self, refuse=False):
        self.refuse = refuse

    def execute_script(self, script, args):
        if self.refuse:
            raise WebDriverException("Activity not started, unable to resolve Intent")


def test_a_delivered_deeplink_completes_deliver():
    deeplink.fire(FiringDriver(), "openid-credential-offer://x", "com.example.wallet")
    assert progress.snapshot()["stage"] == progress.DELIVER


def test_a_refused_deeplink_records_nothing_and_still_raises():
    with pytest.raises(WebDriverException):
        deeplink.fire(FiringDriver(refuse=True), "https://nowhere", "com.example.wallet")
    assert progress.snapshot()["steps"] == []


def _screens(**kw):
    kw.setdefault("name", "testwallet")
    kw.setdefault("home", lambda d, t: False)
    return Screens(**kw)


def _ctx():
    return FlowContext(flow="credential_flow", wallet="testwallet", what="a_credential")


def test_the_awaited_screen_completes_present_and_answered_prompts_are_steps():
    state = {"n": 0}

    def prompt(driver, ctx):
        state["n"] += 1
        return state["n"] == 1  # shows once, then is gone

    shown = {"n": 0}

    def target(driver, timeout):
        shown["n"] += 1
        return shown["n"] > 1

    result = outcome.wait_for(FiringDriver(), _screens(), _ctx(), target=target, timeout=2,
                              interstitials=(Interstitial("device-pin", prompt, max_fires=3),))
    assert result == outcome.SUCCESS
    snap = progress.snapshot()
    assert snap["stage"] == progress.PRESENT
    assert "answered device-pin (1)" in snap["steps"][0]


def test_a_wait_that_never_sees_its_screen_completes_nothing():
    """`absent`: nothing of the wallet's appeared, so it cannot be said to have come forward."""
    outcome.wait_for(FiringDriver(), _screens(), _ctx(), target=lambda d, t: False, timeout=0.5)
    assert progress.snapshot()["stage"] == ""


@pytest.mark.parametrize("seen, expected", [
    ({"error": lambda d, t: True}, outcome.REJECTED),
    ({"home": lambda d, t: True}, outcome.DISMISSED),
], ids=["its error", "its home screen"])
def test_the_wallets_own_screen_proves_it_came_forward(seen, expected):
    """hovi's early rejections and unime's dismissals were drawn at "Wallet opens" — as if the app
    never came forward — while it was in front showing its own screen."""
    state = outcome.wait_for(FiringDriver(), _screens(**seen), _ctx(),
                             target=lambda d, t: False, timeout=1)
    assert state == expected
    assert progress.snapshot()["stage"] == progress.OPEN
    assert report.stopped_after(progress.OPEN, "issuance") == "present", \
        "so the cell stops at the offer it never showed"


def test_a_spinner_proves_it_came_forward_once_not_every_tick():
    outcome.wait_for(FiringDriver(), _screens(processing=lambda d, t: True), _ctx(),
                     target=lambda d, t: False, timeout=0.6)
    opened = [s for s in progress.snapshot()["steps"] if s.startswith("open:")]
    assert opened == ["open: testwallet was in front, processing"]


def test_answered_prompts_and_a_prompt_loop_do_not_prove_the_wallet_came_forward():
    """A permission dialog or the system biometric sheet is Android's, not the wallet's."""
    loop = Interstitial("device-pin", lambda driver, ctx: True, max_fires=1)
    state = outcome.wait_for(FiringDriver(), _screens(), _ctx(), target=lambda d, t: False,
                             timeout=0.5, interstitials=(loop,))
    assert state == outcome.PROMPT_LOOP
    snap = progress.snapshot()
    assert snap["stage"] == ""
    assert snap["steps"] == ["start: answered device-pin (1)"]


def test_a_crash_does_not_prove_the_wallet_came_forward(monkeypatch):
    monkeypatch.setattr(outcome, "_CRASH_SCAN_INTERVAL", 0.0)
    monkeypatch.setattr(outcome, "detect_crash_or_anr", lambda driver, timeout=0.3: object())
    state = outcome.wait_for(FiringDriver(), _screens(), _ctx(), target=lambda d, t: False,
                             timeout=1)
    assert state == outcome.CRASHED
    assert progress.snapshot()["stage"] == ""


def test_a_stale_error_proves_nothing_about_this_case():
    """hovi's banner can outlive the case that raised it; `error_before` makes the loop ignore it,
    and the trail must ignore it too."""
    outcome.wait_for(FiringDriver(), _screens(error=lambda d, t: True), _ctx(),
                     target=lambda d, t: False, timeout=0.5, error_before=True)
    assert progress.snapshot()["stage"] == ""


def test_a_clean_error_surface_once_the_wallet_settled_completes_accept():
    """hovi waits for home before it looks, so a clean surface there is evidence."""
    outcome.raise_if_rejected(FiringDriver(), _screens(error=lambda d, t: False), _ctx(),
                              action="accepted the offer for", settled=True)
    assert progress.snapshot()["stage"] == progress.ACCEPT


def test_a_clean_probe_straight_after_the_tap_is_only_a_step():
    """Two seconds without an error, right after tapping, says little: an error at three seconds
    would be recorded as an accept. unime probes that way."""
    outcome.raise_if_rejected(FiringDriver(), _screens(error=lambda d, t: False), _ctx(),
                              action="accepted the offer for")
    snap = progress.snapshot()
    assert snap["stage"] == ""
    assert "no error within 2s" in snap["steps"][0]


@pytest.mark.parametrize("why, kwargs", [
    ("no error screen is mapped", {"screens": {}, "error_before": False}),
    ("a stale error was already up", {"screens": {"error": lambda d, t: True}, "error_before": True}),
])
def test_an_accept_nobody_could_check_is_a_step_not_the_stage(why, kwargs):
    """Never later than the truth: claiming `accept` here would say the wallet did not object when
    nothing looked."""
    outcome.raise_if_rejected(FiringDriver(), _screens(**kwargs["screens"]), _ctx(),
                              action="accepted the offer for", error_before=kwargs["error_before"])
    snap = progress.snapshot()
    assert snap["stage"] == "", why
    assert "error surface not checkable" in snap["steps"][0]


def test_a_rejection_after_accepting_does_not_complete_accept():
    with pytest.raises(outcome.FlowFailure):
        outcome.raise_if_rejected(FiringDriver(), _screens(error=lambda d, t: True), _ctx(),
                                  action="accepted the offer for")
    assert progress.snapshot()["stage"] == ""


def _request():
    return SimpleNamespace(node=SimpleNamespace(user_properties=[]))


def test_a_count_that_moved_completes_keep():
    credential_count.assert_increased(0, 1, request=_request(), wallet="w", issuer="i", case="c")
    assert progress.snapshot()["stage"] == progress.KEEP


def test_an_unreadable_count_does_not_claim_keep():
    credential_count.assert_increased(None, 1, request=_request(), wallet="w", issuer="i", case="c")
    assert progress.snapshot()["stage"] == ""


def test_a_flat_count_does_not_claim_keep():
    with pytest.raises(outcome.FlowFailure):
        credential_count.assert_increased(1, 1, request=_request(), wallet="w", issuer="i", case="c")
    assert progress.snapshot()["stage"] == ""


# --- the published cell --------------------------------------------------------------------------

@pytest.mark.parametrize("suite, completed, stopped", [
    ("issuance", "deliver", "open"),
    ("issuance", "present", "accept"),        # issuance has no "match"
    ("verification", "present", "match"),
    ("verification", "match", "accept"),
    ("issuance", "keep", "keep"),             # a failure after the last stage stays on it
])
def test_a_cell_stops_at_the_step_after_the_last_one_completed(suite, completed, stopped):
    assert report.stopped_after(completed, suite) == stopped


def _matrix(cell):
    return {"issuance": {"hovi": {"hovi_issuer": cell}}, "verification": {}}


def test_a_measured_stage_overrides_the_categorys_guess():
    """`rejected` is always drawn at "accept" by category; this one was rejected right after the
    deeplink."""
    cell = report.make_cell(
        [{"result": "Failed", "log": "E   base.outcome.FlowFailure: [rejected] x\n"}],
        {"category": "rejected", "stage": "deliver", "steps": ["deliver: deeplink delivered"]})
    stages = report.stage_breakdown(_matrix(cell))["issuance"]["stages"]
    assert stages["open"]["n"] == 1 and stages["accept"]["n"] == 0


def test_an_uncategorised_failure_is_still_placed_when_its_stage_is_known():
    """A bare locator timeout has no category, and used to be counted but not placed."""
    cell = report.make_cell(
        [{"result": "Failed", "log": "E   Exception: Element ('xpath', '//x') not found\n"}],
        {"category": "", "stage": "present", "steps": ["present: offer shown"]})
    data = report.stage_breakdown(_matrix(cell))["issuance"]
    assert data["stages"]["accept"]["n"] == 1
    assert data["stages"]["accept"]["reasons"] == []
    assert data["off_flow"] == 0


@pytest.mark.parametrize("reason", ["lost_session", "harness"])
def test_our_own_failures_stay_off_the_diagram_however_far_the_trail_got(reason):
    """A lost Appium session drawn at "Wallet opens" would be published as the vendor's."""
    cell = report.make_cell(
        [{"result": "Failed", "log": f"E   base.outcome.FlowFailure: [{reason}] x\n"}],
        {"category": reason, "stage": "deliver", "steps": ["deliver: deeplink delivered"]})
    data = report.stage_breakdown(_matrix(cell))["issuance"]
    assert data["off_flow"] == 1
    assert all(s["n"] == 0 for s in data["stages"].values())


def test_a_crash_is_placed_where_it_happened():
    cell = report.make_cell(
        [{"result": "Failed", "log": "E   base.outcome.FlowFailure: [crashed] x\n"}],
        {"category": "crashed", "stage": "present", "steps": ["present: offer shown"]})
    assert report.stage_breakdown(_matrix(cell))["issuance"]["stages"]["accept"]["n"] == 1


def test_a_teardown_error_after_a_pass_does_not_inherit_the_passs_trail():
    """The test got to `keep` and passed; drawing its teardown error there blames the wallet."""
    import conftest as root_conftest

    item = SimpleNamespace(nodeid="w/tests/t.py::test_x[hovi]", name="test_x[hovi]",
                           config=SimpleNamespace(),
                           callspec=SimpleNamespace(params={"driver": "hovi"}))
    rep = lambda when, failed=False: SimpleNamespace(when=when, failed=failed, skipped=False,
                                                     duration=1.0)
    root_conftest._record_result(item, rep("setup"))
    root_conftest._record_result(item, rep("call"))
    item._failure_line = "InvalidSessionIdException: A session is either terminated"
    item._failure_trail = {"stage": "keep", "steps": ["keep: count 0 → 1"]}
    root_conftest._record_result(item, rep("teardown", failed=True))

    record = item.config._results[item.nodeid]
    assert record["outcome"] == "error" and record["failed_in"] == "teardown"
    assert record["stage"] == "" and record["steps"] == []


def test_a_teardown_error_after_a_failed_call_keeps_the_calls_trail():
    """The teardown branch clears the trail only for a pass; the call's verdict and its position
    must survive a second exception in teardown."""
    import conftest as root_conftest

    item = SimpleNamespace(nodeid="w/tests/t.py::test_z[hovi]", name="test_z[hovi]",
                           config=SimpleNamespace(),
                           callspec=SimpleNamespace(params={"driver": "hovi"}))
    rep = lambda when, failed=False: SimpleNamespace(when=when, failed=failed, skipped=False,
                                                     duration=1.0)
    root_conftest._record_result(item, rep("setup"))
    item._failure_line = "FlowFailure: [rejected] hovi refused"
    item._failure_category = "rejected"
    item._failure_trail = {"stage": "present", "steps": ["present: offer shown"]}
    root_conftest._record_result(item, rep("call", failed=True))
    root_conftest._record_result(item, rep("teardown", failed=True))

    record = item.config._results[item.nodeid]
    assert record["failed_in"] == "call"
    assert record["stage"] == "present" and record["steps"] == ["present: offer shown"]


def test_a_retry_that_passes_clears_the_failed_attempts_trail():
    import conftest as root_conftest

    item = SimpleNamespace(nodeid="w/tests/t.py::test_y[hovi]", name="test_y[hovi]",
                           config=SimpleNamespace(),
                           callspec=SimpleNamespace(params={"driver": "hovi"}))
    rep = lambda when, failed=False: SimpleNamespace(when=when, failed=failed, skipped=False,
                                                     duration=1.0)
    root_conftest._record_result(item, rep("setup"))
    item._failure_trail = {"stage": "present", "steps": ["present: offer shown"]}
    root_conftest._record_result(item, rep("call", failed=True))
    assert item.config._results[item.nodeid]["stage"] == "present"

    root_conftest._record_result(item, rep("setup"))
    root_conftest._record_result(item, rep("call"))
    record = item.config._results[item.nodeid]
    assert record["outcome"] == "passed" and record["stage"] == "" and record["steps"] == []


def test_without_a_trail_the_category_still_places_the_cell():
    cell = report.make_cell(
        [{"result": "Failed", "log": "E   base.outcome.FlowFailure: [rejected] x\n"}],
        {"category": "rejected", "stage": "", "steps": []})
    assert report.stage_breakdown(_matrix(cell))["issuance"]["stages"]["accept"]["n"] == 1


def test_a_passing_cell_carries_no_stage():
    cell = report.make_cell([{"result": "Passed", "log": ""}], {"stage": "keep", "steps": ["x"]})
    assert cell["stage"] is None and cell["steps"] == []


def test_the_tooltip_says_how_far_it_got_in_the_suites_own_words(tmp_path):
    tid = ("wallets/hovi/tests/test_credential_verification.py::test_credential_verification["
           "hovi_verifier/pension_verification-hovi]")
    wallet = tmp_path / "run" / "hovi"
    wallet.mkdir(parents=True)
    log = "E   base.outcome.FlowFailure: [no_match] x\n"
    blob = html.escape(json.dumps({"tests": {tid: [{"result": "Failed", "log": log}]}}), quote=True)
    (wallet / "report.html").write_text(f'<div data-jsonblob="{blob}"></div>')
    (wallet / "summary.json").write_text(json.dumps({"tests": [{
        "nodeid": tid, "category": "no_match", "stage": "present",
        "steps": ["deliver: deeplink delivered", "present: request shown"]}]}))

    cell = report.collect(wallet.parent)["verification"]["hovi"]["hovi_verifier"]
    assert cell["reached_label"] == "Request shown", "the verification wording, not issuance's"
    assert "Got as far as: Request shown (2 steps recorded)." in report._outcome_td(cell)


# --- app.log --------------------------------------------------------------------------------------

def _digest_request(tmp_path, trail):
    node = SimpleNamespace(name="test_x", nodeid="x::test_x", rep_call=SimpleNamespace(failed=True),
                           _failure_line="RuntimeError: boom", _failure_category="")
    if trail is not None:
        node._failure_trail = trail
    return SimpleNamespace(config=SimpleNamespace(_run_dir=tmp_path), node=node)


class _Driver:
    page_source = "<hierarchy />"


def _reached(tmp_path):
    for line in (tmp_path / "app.log").read_text().splitlines():
        if line.startswith("reached:"):
            return line.split(":", 1)[1].strip()
    raise AssertionError("no reached: line")


def test_the_digest_says_how_far_the_flow_got(tmp_path):
    trail = {"stage": "present", "steps": ["deliver: deeplink delivered", "present: offer shown"]}
    record_error_screen(_Driver(), _digest_request(tmp_path, trail), {"application": {}},
                        wallet="hovi")
    assert _reached(tmp_path) == "present — deliver: deeplink delivered → present: offer shown"


@pytest.mark.parametrize("trail", [None, {"stage": "", "steps": []}],
                         ids=["no snapshot", "empty snapshot"])
def test_the_digest_does_not_claim_a_flow_without_a_trail_got_nowhere(tmp_path, trail):
    """Most wallets record no progress yet; an empty trail from them is not "failed at the start".
    The empty snapshot is what real runs produce — the hook always takes one."""
    record_error_screen(_Driver(), _digest_request(tmp_path, trail), {"application": {}},
                        wallet="hovi")
    assert _reached(tmp_path) == "(no completed step recorded)"


def test_every_flow_on_the_shared_wait_loop_fires_through_the_recorder():
    """A flow on `outcome.wait_for` that fires `mobile: deepLink` itself never records `deliver`,
    and no device-free test would notice. Checked on the source, which is all there is to check
    without a device."""
    offenders = []
    for flow in sorted(ROOT.glob("wallets/*/flows/*.py")):
        text = flow.read_text()
        if "outcome.wait_for(" in text and '"mobile: deepLink"' in text:
            offenders.append(str(flow.relative_to(ROOT)))
    assert not offenders, f"fire the deeplink with base/deeplink.fire instead: {offenders}"


# --- through the real root conftest ----------------------------------------------------------------

def test_the_trail_reaches_summary_json_and_starts_empty_on_every_attempt(tmp_path):
    """A child session against the real root conftest: the hook reads the trail for a bare
    exception, and a rerun must not inherit the attempt before it."""
    out = tmp_path / "out.json"
    case = Path(__file__).parent / "hookcheck" / "progress_case.py"
    result = subprocess.run(
        [sys.executable, "-m", "pytest", str(case), "-q", "--reruns", "0", "-p", "no:cacheprovider"],
        cwd=ROOT, env={**os.environ, "HOOKCHECK_OUT": str(out), "TMPDIR": str(tmp_path)},
        capture_output=True, text=True, timeout=180,
    )
    assert out.exists(), f"the child session never reached teardown:\n{result.stdout[-3000:]}"
    run_dir = Path(json.loads(out.read_text())["run_dir"])
    records = {t["name"]: t for t in json.loads((run_dir / "summary.json").read_text())["tests"]}

    bare = records["test_a_bare_failure_after_two_stages"]
    assert bare["category"] == "", "a bare exception carries no category…"
    assert bare["stage"] == "present", "…but its position is still known"
    assert bare["steps"] == ["deliver: deeplink delivered", "present: offer shown"]

    rerun = records["test_a_rerun_that_fails_at_once"]
    assert rerun["attempts"] == 2
    assert rerun["stage"] == "" and rerun["steps"] == [], (
        "attempt 2 inherited attempt 1's trail — it reached nothing itself"
    )
    # app.log is written by the `app` fixture's capture path, which this case has no need of; the
    # digest's `reached:` line is covered above with the trail handed to it directly.
