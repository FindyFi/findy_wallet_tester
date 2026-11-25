"""navigate_to_home types every failure of init_flow, so no wallet column publishes as bare Error."""
from types import SimpleNamespace

import pytest
from selenium.common.exceptions import (
    NoSuchElementException, StaleElementReferenceException, TimeoutException, WebDriverException,
)

from base import outcome
from base.conftest_helpers import navigate_to_home
from base.outcome import FlowFailure


@pytest.fixture(autouse=True)
def no_overlay(monkeypatch):
    monkeypatch.setattr("base.conftest_helpers.detect_crash_or_anr", lambda driver: None)


class Flow:
    def __init__(self, exc=None):
        self.exc = exc

    def run(self, driver, **kw):
        if self.exc:
            raise self.exc


def _app(skip_if_done):
    return SimpleNamespace(driver=object(), page_args={}, config={
        "application": {"pin": "1", "package": "p"},
        "onboarding": {"skip_if_done": skip_if_done}})


def _go(exc, skip_if_done, config=None, nodeid="t0"):
    req = SimpleNamespace(config=config or SimpleNamespace(), node=SimpleNamespace(nodeid=nodeid))
    navigate_to_home(_app(skip_if_done), req, Flow(exc))


CASES = [
    (RuntimeError("Home screen not found after reset"), outcome.SETUP, "Home screen not found"),
    (RuntimeError("App stuck in unknown state even after restart"), outcome.SETUP, "App stuck"),
    (TimeoutException("waited"), outcome.SETUP, "Timed out while"),
    (NoSuchElementException("no el"), outcome.SETUP, "A screen element was not where"),
    (StaleElementReferenceException("stale"), outcome.SETUP, "A screen element was not where"),
    (WebDriverException("instrumentation process is not running"), outcome.LOST_SESSION,
     "instrumentation process is not running"),
]


@pytest.mark.parametrize("skip_if_done", [False, True])
@pytest.mark.parametrize("exc,category,text", CASES)
def test_each_exception_kind_maps_to_its_category(exc, category, text, skip_if_done):
    with pytest.raises(FlowFailure) as got:
        _go(exc, skip_if_done)
    assert got.value.category == category
    assert text in str(got.value)
    assert got.value.__cause__ is exc


@pytest.mark.parametrize("skip_if_done", [False, True])
def test_a_flow_failure_passes_through_unchanged(skip_if_done):
    original = FlowFailure(outcome.REJECTED, "toppan said no")
    with pytest.raises(FlowFailure) as got:
        _go(original, skip_if_done)
    assert got.value is original


def test_the_follow_on_keeps_the_first_failures_category():
    config = SimpleNamespace()
    with pytest.raises(FlowFailure):
        _go(WebDriverException("dead"), False, config, "t0")
    with pytest.raises(FlowFailure) as got:
        _go(None, False, config, "t1")
    assert got.value.category == outcome.LOST_SESSION
    assert "Session reset already failed for this wallet earlier in the run — not retrying." \
        in str(got.value)
    assert "First failure: WebDriverException" in str(got.value)


def test_the_follow_on_of_an_untyped_first_failure_is_setup():
    config = SimpleNamespace()
    with pytest.raises(FlowFailure):
        _go(RuntimeError("stuck"), False, config, "t0")
    with pytest.raises(FlowFailure) as got:
        _go(None, False, config, "t1")
    assert got.value.category == outcome.SETUP


def test_a_timeout_reads_its_first_line_not_an_empty_message():
    exc = TimeoutException("Message: \nStacktrace:\n  at foo")
    with pytest.raises(FlowFailure) as got:
        _go(exc, False)
    text = str(got.value)
    assert "Timed out while resetting the wallet: Message:" in text
    assert "Stacktrace" not in text


def test_a_plain_runtime_error_text_is_verbatim():
    with pytest.raises(FlowFailure) as got:
        _go(RuntimeError("App stuck in unknown state even after restart"), True)
    assert str(got.value) == "[setup] App stuck in unknown state even after restart"


@pytest.mark.parametrize("overlay,word", [("APP_CRASH", "crashed"), ("ANR", "stopped responding")])
def test_a_crash_dialog_makes_it_crashed_not_setup(monkeypatch, overlay, word):
    from base.android import SystemOverlay
    monkeypatch.setattr("base.conftest_helpers.detect_crash_or_anr",
                        lambda driver: getattr(SystemOverlay, overlay))
    with pytest.raises(FlowFailure) as got:
        _go(RuntimeError("Home screen not found after reset"), False)
    assert got.value.category == outcome.CRASHED
    assert word in str(got.value)
    assert "Home screen not found after reset" in str(got.value)


def test_a_dead_session_during_the_crash_look_is_lost_session(monkeypatch):
    def dead(driver):
        raise WebDriverException("gone")
    monkeypatch.setattr("base.conftest_helpers.detect_crash_or_anr", dead)
    with pytest.raises(FlowFailure) as got:
        _go(RuntimeError("stuck"), True)
    assert got.value.category == outcome.LOST_SESSION


def test_the_follow_on_is_no_retry():
    config = SimpleNamespace()
    with pytest.raises(FlowFailure):
        _go(RuntimeError("stuck"), False, config, "t0")
    with pytest.raises(FlowFailure) as got:
        _go(None, False, config, "t1")
    assert str(got.value).endswith("[no_retry]")
