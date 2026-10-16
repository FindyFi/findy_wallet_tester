"""procivis is put in the background before a verification deeplink (flows/_background.py).

Why is SUSPECTED, not established: see that module's docstring. These tests hold what it does —
HOME first, a bounded wait on Android's own app state, never a failed test because of it — and that
issuance was left alone. Nothing here touches a device.
"""
from pathlib import Path
from types import SimpleNamespace

import pytest
from selenium.common.exceptions import WebDriverException

from _procivis_fakes import PACKAGE, URL, Driver
from base import deeplink, outcome, progress
from wallets.procivis.flows import _background, _failures as failures, verification_flow as vf


class _Runaway(BaseException):
    """Not an Exception, so nothing in the flow can swallow it: an unbounded wait fails the test
    instead of hanging it."""


class _Clock:
    """Fake time for `_background`: sleeping advances it, so a bounded wait is instant here."""

    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class _Polled(Driver):
    """`states` answers `query_app_state` in turn (the last repeats; an exception is raised).
    `calls` records the order of HOME, the state polls and the deeplink."""

    def __init__(self, states=(3,), **kw):
        super().__init__(**kw)
        self.states = list(states)
        self.calls = []

    def press_keycode(self, code):
        self.calls.append(("key", code))

    def query_app_state(self, app_id):
        self.calls.append(("state", app_id))
        if sum(1 for c in self.calls if c[0] == "state") > 500:
            raise _Runaway("the background wait has no bound")
        answer = self.states.pop(0) if len(self.states) > 1 else self.states[0]
        if isinstance(answer, BaseException):
            raise answer
        return answer

    def execute_script(self, script, args):
        self.calls.append(("fire", args.get("url")))
        super().execute_script(script, args)


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    """The rest of the verification flow, faked to succeed: only the backgrounding is real."""
    progress.reset()
    monkeypatch.setattr(failures, "unlock", lambda d, ctx: None)
    monkeypatch.setattr(vf, "sharing_on_screen", lambda d, timeout=2: True)
    monkeypatch.setattr(vf, "ProofRequestSharingPage",
                        lambda d, **kw: SimpleNamespace(share=lambda: None))
    monkeypatch.setattr(vf, "wait_present", lambda d, loc, timeout=2: True)
    monkeypatch.setattr(vf, "ProofRequestAcceptProcessPage",
                        lambda d, **kw: SimpleNamespace(wait_for_result=lambda: True))
    monkeypatch.setattr(deeplink, "unroutable_reason", lambda *a, **k: "")
    monkeypatch.setattr(deeplink, "registered_schemes", lambda *a, **k: [])
    monkeypatch.setattr(outcome, "detect_crash_or_anr", lambda *a, **k: None)
    yield
    progress.reset()


@pytest.fixture
def clock(monkeypatch):
    c = _Clock()
    monkeypatch.setattr(_background, "time", c)
    return c


def _verify(driver):
    vf.run(driver, provider=SimpleNamespace(get=lambda n: URL),
           credential_name="pension_verification", app_package=PACKAGE,
           pin="123456", timeouts={"credential_offer": 1})


def _order(driver):
    return [c[0] for c in driver.calls]


def test_home_is_pressed_and_confirmed_before_the_deeplink_is_fired(clock):
    driver = _Polled(states=(3,))
    _verify(driver)
    assert driver.calls[0] == ("key", 3), "Android's KEYCODE_HOME"
    assert _order(driver) == ["key", "state", "fire"]
    steps = progress.snapshot()["steps"]
    assert steps[0] == "start: backgrounded procivis before the deeplink", "a step, not a stage"


def test_the_wait_polls_until_procivis_leaves_the_foreground(clock):
    driver = _Polled(states=(4, 4, 3))
    _verify(driver)
    assert _order(driver) == ["key", "state", "state", "state", "fire"]
    assert clock.now == pytest.approx(2 * _background.POLL), "polled, not slept blind"


def test_a_state_stuck_in_the_foreground_is_bounded_and_still_fires(clock, caplog):
    driver = _Polled(states=(4,))
    _verify(driver)
    polls = _order(driver).count("state")
    assert 2 <= polls <= _background.TIMEOUT / _background.POLL + 2
    assert clock.now >= _background.TIMEOUT
    assert _order(driver)[-1] == "fire"
    assert "still reported foreground" in caplog.text
    assert any("still reported foreground" in s for s in progress.snapshot()["steps"])


def test_an_unreadable_app_state_costs_nothing_and_still_fires(clock, caplog):
    driver = _Polled(states=(WebDriverException("unknown command: mobile: queryAppState"),))
    _verify(driver)
    assert _order(driver) == ["key", "state", "fire"]
    assert progress.snapshot()["stage"] == progress.ACCEPT, "the flow ran on as normal"
    assert "Could not read procivis's app state" in caplog.text


def test_a_home_press_that_fails_still_fires(clock):
    class NoKeys(_Polled):
        def press_keycode(self, code):
            raise WebDriverException("keyevent failed")

    driver = NoKeys()
    _verify(driver)
    assert _order(driver) == ["fire"]


def test_issuance_still_fires_from_the_foreground():
    """One change at a time: only verification backgrounds first, until a device run shows it
    helps. This pins that the issuance flow was left alone."""
    src = (Path(__file__).parents[2] / "wallets" / "procivis" / "flows" / "credential_flow.py").read_text()
    assert "press_keycode" not in src and "_background" not in src
