"""The helpers every wallet's flows share for a dead session, a crash dialog and permission dialogs.

They moved here from four wallets' flows/_failures.py, and every message they raise is a published
contract, so most of what follows pins exact text. Nothing here touches a device: the driver is a
stand-in, the Android probes are patched on `base.flow_failures` (the names the helpers call), and
the clock is a virtual one.
"""
import pytest
from selenium.common.exceptions import WebDriverException

from base import flow_failures, outcome, progress
from base.android import SystemOverlay
from base.flow_context import FlowContext
from base.outcome import FlowFailure

DRIVER = object()


def ctx(wallet="paradym", flow="credential_flow", what="PID"):
    return FlowContext(flow=flow, wallet=wallet, what=what)


def said(failure):
    """The sentence a FlowFailure carries, without the `[category]` prefix its text adds."""
    return str(failure).split("] ", 1)[1]


def dead(msg="session is gone"):
    return WebDriverException(msg)


def raising(exc):
    def probe(*_a, **_k):
        raise exc
    return probe


class Clock:
    """A virtual time.monotonic. Every probe call spends `step` seconds of it, and the test
    fails rather than hangs if a loop is still going after `limit` calls."""

    def __init__(self, step=1.0, limit=200):
        self.now, self.step, self.limit, self.calls = 0.0, step, limit, 0

    def monotonic(self):
        return self.now

    def probe(self, shows=False):
        def look(driver, wait):
            self.calls += 1
            assert self.calls < self.limit, "the loop ignored the virtual clock"
            self.now += self.step
            return shows
        return look


# --- lost_session / lost -------------------------------------------------------------------------

def test_lost_session_with_what_is_the_exact_sentence():
    f = flow_failures.lost_session("credential_flow", "mid-wait", "boom", "PID")
    assert f.category == outcome.LOST_SESSION
    assert said(f) == ("[credential_flow] Appium/UiAutomator2 connection lost mid-wait for 'PID' "
                       "(app may have crashed): boom")
    assert str(f).startswith("[lost_session] [credential_flow]")


def test_lost_session_without_what_leaves_the_for_clause_out():
    f = flow_failures.lost_session("verification_flow", "after sharing", "None")
    assert said(f) == ("[verification_flow] Appium/UiAutomator2 connection lost after sharing "
                     "(app may have crashed): None")


def test_lost_session_with_empty_what_keeps_an_empty_quoted_clause():
    f = flow_failures.lost_session("f", "mid-wait", "x", "")
    assert said(f) == "[f] Appium/UiAutomator2 connection lost mid-wait for '' (app may have crashed): x"


def test_lost_session_uses_the_text_it_is_given_not_one_derived_from_an_exception():
    # heidi/authbound pass exc.msg, which is None for a bare WebDriverException.
    exc = WebDriverException()
    assert exc.msg is None
    f = flow_failures.lost_session("f", "mid-wait", exc.msg)
    assert said(f).endswith("(app may have crashed): None")
    assert "Message" not in said(f)


def test_lost_session_does_not_look_inside_what_it_is_given():
    # `said` is already the chosen text: an exception object is formatted as it is, not unpacked.
    exc = dead("inner")
    f = flow_failures.lost_session("f", "mid-wait", exc)
    assert said(f).endswith("(app may have crashed): " + str(exc))
    assert "Message: inner" in said(f)


def test_lost_reads_flow_and_what_from_the_context():
    f = flow_failures.lost(ctx(flow="verification_flow", what="Mortgage"), "while waiting",
                           dead("gone"))
    assert f.category == outcome.LOST_SESSION
    assert said(f) == ("[verification_flow] Appium/UiAutomator2 connection lost while waiting for "
                     "'Mortgage' (app may have crashed): gone")


def test_lost_with_empty_what_says_for_empty_quotes():
    f = flow_failures.lost(ctx(what=""), "while waiting", dead("gone"))
    assert "lost while waiting for '' (app may" in said(f)


def test_lost_falls_back_to_str_of_the_exception_when_it_has_no_msg():
    f = flow_failures.lost(ctx(), "while waiting", ValueError("plain"))
    assert said(f).endswith("(app may have crashed): plain")
    g = flow_failures.lost(ctx(), "while waiting", WebDriverException())
    assert said(g).endswith("(app may have crashed): " + str(WebDriverException()))


# --- raise_if_crashed ----------------------------------------------------------------------------

def test_raise_if_crashed_returns_when_no_dialog(monkeypatch):
    monkeypatch.setattr(flow_failures, "detect_crash_or_anr", lambda d: None)
    assert flow_failures.raise_if_crashed(DRIVER, ctx(), "after Accept") is None


def test_raise_if_crashed_names_a_crash_dialog(monkeypatch):
    monkeypatch.setattr(flow_failures, "detect_crash_or_anr", lambda d: SystemOverlay.APP_CRASH)
    with pytest.raises(FlowFailure) as e:
        flow_failures.raise_if_crashed(DRIVER, ctx(), "after Accept")
    assert e.value.category == outcome.CRASHED
    assert said(e.value) == ("[credential_flow] paradym crashed after Accept for 'PID': "
                           "Android showed its crash dialog")


def test_raise_if_crashed_names_a_not_responding_dialog(monkeypatch):
    monkeypatch.setattr(flow_failures, "detect_crash_or_anr", lambda d: SystemOverlay.ANR)
    with pytest.raises(FlowFailure) as e:
        flow_failures.raise_if_crashed(DRIVER, ctx(), "after Accept")
    assert e.value.category == outcome.CRASHED
    assert said(e.value) == ("[credential_flow] paradym stopped responding after Accept for 'PID': "
                           "Android showed its not-responding dialog")


@pytest.mark.parametrize("wallet", ["paradym", "gataca", "toppan", "procivis"])
def test_raise_if_crashed_names_the_wallet_from_the_context(monkeypatch, wallet):
    monkeypatch.setattr(flow_failures, "detect_crash_or_anr", lambda d: SystemOverlay.APP_CRASH)
    with pytest.raises(FlowFailure) as e:
        flow_failures.raise_if_crashed(DRIVER, ctx(wallet=wallet), "later")
    assert said(e.value).startswith(f"[credential_flow] {wallet} crashed later")


def test_raise_if_crashed_turns_a_dead_session_into_lost_session(monkeypatch):
    monkeypatch.setattr(flow_failures, "detect_crash_or_anr", raising(dead("gone")))
    with pytest.raises(FlowFailure) as e:
        flow_failures.raise_if_crashed(DRIVER, ctx(), "after Accept")
    assert e.value.category == outcome.LOST_SESSION
    assert said(e.value) == ("[credential_flow] Appium/UiAutomator2 connection lost while looking "
                           "for a crash dialog after Accept for 'PID' (app may have crashed): gone")


def test_raise_if_crashed_only_logs_any_other_failure(monkeypatch, caplog):
    monkeypatch.setattr(flow_failures, "detect_crash_or_anr", raising(RuntimeError("odd")))
    with caplog.at_level("WARNING"):
        assert flow_failures.raise_if_crashed(DRIVER, ctx(), "later") is None
    assert "[credential_flow] Could not look for a crash dialog: odd" in caplog.messages


# --- first_of ------------------------------------------------------------------------------------

def test_first_of_returns_the_first_screen_in_order_that_shows():
    seen = []

    def probe(name, shows):
        def look(driver, wait):
            seen.append(name)
            return shows
        return look

    screens = [("a", probe("a", False), 1), ("b", probe("b", True), 1), ("c", probe("c", True), 1)]
    assert flow_failures.first_of(DRIVER, ctx(), screens, 5, during="x") == "b"
    assert seen == ["a", "b"]


def test_first_of_runs_every_probe_once_even_at_timeout_zero():
    seen = []

    def probe(name):
        def look(driver, wait):
            seen.append(name)
            return False
        return look

    screens = [("a", probe("a"), 1), ("b", probe("b"), 1), ("c", probe("c"), 1)]
    assert flow_failures.first_of(DRIVER, ctx(), screens, 0, during="x") == ""
    assert seen == ["a", "b", "c"]


def test_first_of_gives_each_probe_its_own_timeout():
    waits = []

    def look(driver, wait):
        waits.append(wait)
        return False

    flow_failures.first_of(DRIVER, ctx(), [("a", look, 0.3), ("b", look, 2.5)], 0, during="x")
    assert waits == [0.3, 2.5]


def test_first_of_gives_up_with_empty_string_once_the_deadline_passes(monkeypatch):
    clock = Clock(step=1.0)
    monkeypatch.setattr(flow_failures.time, "monotonic", clock.monotonic)
    assert flow_failures.first_of(DRIVER, ctx(), [("a", clock.probe(False), 1)], 3.5,
                                  during="x") == ""
    assert clock.calls == 4          # passes at t=0,1,2,3 end at t=4 >= 3.5, not before


def test_first_of_reads_the_clock_through_time_monotonic(monkeypatch):
    # A large timeout on the virtual clock must end by it, not by a clock imported by name.
    clock = Clock(step=100.0, limit=50)
    monkeypatch.setattr(flow_failures.time, "monotonic", clock.monotonic)
    assert flow_failures.first_of(DRIVER, ctx(), [("a", clock.probe(False), 1)], 250,
                                  during="x") == ""
    assert clock.calls == 3


def test_first_of_makes_a_dead_session_lost_session_with_the_callers_words():
    screens = [("a", raising(dead("gone")), 1)]
    with pytest.raises(FlowFailure) as e:
        flow_failures.first_of(DRIVER, ctx(), screens, 5, during="while waiting for the offer")
    assert e.value.category == outcome.LOST_SESSION
    assert said(e.value) == ("[credential_flow] Appium/UiAutomator2 connection lost while waiting "
                           "for the offer for 'PID' (app may have crashed): gone")


# --- answer_permission ---------------------------------------------------------------------------

@pytest.fixture
def trail(monkeypatch):
    steps = []
    monkeypatch.setattr(progress, "step", steps.append)
    return steps


def test_answer_permission_records_a_step_when_it_answered(monkeypatch, trail):
    monkeypatch.setattr(flow_failures, "handle_permission_if_present", lambda d: True)
    flow_failures.answer_permission(DRIVER, ctx())
    assert trail == ["answered an Android permission dialog"]


def test_answer_permission_records_nothing_when_no_dialog(monkeypatch, trail):
    monkeypatch.setattr(flow_failures, "handle_permission_if_present", lambda d: False)
    flow_failures.answer_permission(DRIVER, ctx())
    assert trail == []


def test_answer_permission_makes_a_dead_session_lost_session(monkeypatch, trail):
    monkeypatch.setattr(flow_failures, "handle_permission_if_present", raising(dead("gone")))
    with pytest.raises(FlowFailure) as e:
        flow_failures.answer_permission(DRIVER, ctx())
    assert e.value.category == outcome.LOST_SESSION
    assert said(e.value) == ("[credential_flow] Appium/UiAutomator2 connection lost while looking "
                           "for a permission dialog for 'PID' (app may have crashed): gone")
    assert trail == []


# --- late_look -----------------------------------------------------------------------------------

def test_late_look_shares_one_late_second_between_the_screens():
    waits = []

    def look(driver, wait):
        waits.append(wait)
        return False

    screens = [("a", look, 9), ("b", look, 9), ("c", look, 9), ("d", look, 9)]
    assert flow_failures.late_look(DRIVER, ctx(), screens, during="x") == ""
    assert waits == [flow_failures.LATE / 4] * 4


def test_late_look_returns_the_first_name_showing_and_stops():
    waits = []

    def probe(shows):
        def look(driver, wait):
            waits.append(wait)
            return shows
        return look

    screens = [("a", probe(False), 1), ("b", probe(True), 1), ("c", probe(True), 1)]
    assert flow_failures.late_look(DRIVER, ctx(), screens, during="x") == "b"
    assert len(waits) == 2


def test_late_look_makes_a_dead_session_lost_session():
    with pytest.raises(FlowFailure) as e:
        flow_failures.late_look(DRIVER, ctx(), [("a", raising(dead("gone")), 1)],
                                during="while taking a last look")
    assert said(e.value) == ("[credential_flow] Appium/UiAutomator2 connection lost while taking a "
                           "last look for 'PID' (app may have crashed): gone")


def test_the_constants_are_the_ones_the_wallets_had():
    assert flow_failures.LATE == 1.0
    assert flow_failures.APP_STATES == {
        0: "not installed", 1: "not running", 2: "running in the background, suspended",
        3: "running in the background", 4: "running in the foreground"}


# --- what the lost_session keeps, and what passes through ----------------------------------------

def _dead_calls(monkeypatch, exc):
    """Each helper meeting `exc` from its probe, as a zero-argument call."""
    monkeypatch.setattr(flow_failures, "detect_crash_or_anr", raising(exc))
    monkeypatch.setattr(flow_failures, "handle_permission_if_present", raising(exc))
    screens = [("a", raising(exc), 1)]
    return {
        "raise_if_crashed": lambda: flow_failures.raise_if_crashed(DRIVER, ctx(), "later"),
        "answer_permission": lambda: flow_failures.answer_permission(DRIVER, ctx()),
        "first_of": lambda: flow_failures.first_of(DRIVER, ctx(), screens, 5, during="x"),
        "late_look": lambda: flow_failures.late_look(DRIVER, ctx(), screens, during="x"),
    }


@pytest.mark.parametrize("helper", ["raise_if_crashed", "answer_permission", "first_of",
                                    "late_look"])
def test_a_lost_session_is_chained_to_the_drivers_exception(monkeypatch, helper):
    exc = dead("gone")
    with pytest.raises(FlowFailure) as e:
        _dead_calls(monkeypatch, exc)[helper]()
    assert e.value.__cause__ is exc


@pytest.mark.parametrize("helper", ["answer_permission", "first_of", "late_look"])
def test_any_other_failure_of_a_probe_passes_through_unchanged(monkeypatch, helper):
    exc = RuntimeError("odd")
    with pytest.raises(RuntimeError) as e:
        _dead_calls(monkeypatch, exc)[helper]()
    assert e.value is exc
