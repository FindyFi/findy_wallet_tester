"""After a reset toppan shows its language modal late; the reset waits for it or for home.

reports/2026-11-20_14-40-17/toppan: the modal was not seen ~12 s after the reset and up by ~22 s (both runs), init_flow's
single 10 s look gave up and expected home, and every test then ran under the modal.
"""
import pytest

from base import outcome
from base.outcome import FlowFailure
from wallets.toppan.flows import init_flow

PACKAGE = "com.toppansecurity.walletapp"


class Reset:
    """A wiped toppan on a virtual clock. The modal shows at `modal_at`, home at `home_at`."""

    def __init__(self, modal_at=None, home_at=None):
        self.now, self.modal_at, self.home_at = 0.0, modal_at, home_at
        self.answered = False
        self.script = []

    def _up(self, at):
        return at is not None and self.now >= at

    def language(self, driver, timeout=2):
        if self.modal_at is not None and not self.answered and self.now + timeout >= self.modal_at:
            self.now = max(self.now, self.modal_at)
            return True
        self.now += timeout
        return False

    def home(self, driver, locator, timeout=2):
        if self._up(self.home_at) and (not self._up(self.modal_at) or self.answered):
            return True
        self.now += timeout
        return False


class Driver:
    current_package = PACKAGE

    def execute_script(self, *a, **k):
        pass

    def terminate_app(self, package):
        pass

    def activate_app(self, package):
        pass


@pytest.fixture
def reset(monkeypatch):
    def make(modal_at=None, home_at=None):
        r = Reset(modal_at, home_at)
        monkeypatch.setattr(init_flow, "_detect_state", lambda d, timeout=2: "home")
        monkeypatch.setattr(init_flow.language_page, "on_screen", r.language)
        monkeypatch.setattr(init_flow, "wait_present", r.home)
        monkeypatch.setattr(init_flow.time, "monotonic", lambda: r.now)

        def onboard(driver, default_timeout, **kw):
            r.answered = True
            r.script.append("onboard")
        monkeypatch.setattr(init_flow, "_onboard", onboard)

        class Wait:
            def __init__(self, d, t):
                pass

            def until(self, cond):
                if not r.home(None, None, 0):
                    from selenium.common.exceptions import TimeoutException
                    raise TimeoutException()
        monkeypatch.setattr(init_flow, "WebDriverWait", Wait)
        return r
    return make


def _run(**kw):
    init_flow.run(Driver(), skip_if_done=False, app_package=PACKAGE,
                  timeouts={"default": 10, **kw})


def test_a_dialog_that_appears_late_is_answered_then_home(reset):
    r = reset(modal_at=45.0, home_at=45.0)
    _run()
    assert r.script == ["onboard"]
    assert r.answered


def test_home_directly_taps_nothing(reset):
    r = reset(home_at=0.0)
    _run()
    assert r.script == []


def test_neither_is_a_typed_setup_failure(reset):
    reset()
    with pytest.raises(FlowFailure) as exc:
        _run()
    assert exc.value.category == outcome.SETUP
    assert "Home screen not found after reset" in str(exc.value)


def test_home_then_the_modal_a_moment_later_is_still_answered(reset):
    """A build that renders home before presenting the alert must not leave the modal up."""
    r = reset(modal_at=2.0, home_at=0.0)
    _run()
    assert r.script == ["onboard"]


def test_a_dead_session_while_waiting_surfaces_as_lost_session(reset, monkeypatch):
    from types import SimpleNamespace
    from selenium.common.exceptions import WebDriverException
    from base.conftest_helpers import navigate_to_home
    reset(home_at=0.0)

    def dead(driver, timeout=2):
        raise WebDriverException("instrumentation process is not running")
    monkeypatch.setattr(init_flow.language_page, "on_screen", dead)
    app = SimpleNamespace(driver=Driver(), page_args={"timeouts": {"default": 10}}, config={
        "application": {"pin": "1", "package": PACKAGE}, "onboarding": {"skip_if_done": False}})
    req = SimpleNamespace(config=SimpleNamespace(), node=SimpleNamespace(nodeid="t"))
    with pytest.raises(FlowFailure) as exc:
        navigate_to_home(app, req, init_flow)
    assert exc.value.category == outcome.LOST_SESSION


def test_the_modal_is_not_waited_for_past_the_overall_timeout(reset):
    reset(modal_at=500.0, home_at=500.0)
    with pytest.raises(FlowFailure):
        _run()
