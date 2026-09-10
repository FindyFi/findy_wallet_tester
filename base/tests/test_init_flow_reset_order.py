"""A reset wipes first and never probes the app's state.

On 2026-09-09 hovi crash-looped on every launch — a credential persisted from a rejected paradym
offer kills `CredentialCard` on the home render — and all 10 hovi cells errored in fixture setup
with `HOVI_RESET=true` set. The wipe lived in the `else` of `if landing / elif home`, so it was
reachable only *after* reaching a known screen, which is precisely what a bricked wallet cannot do.
`_back_to_known_state` exhausted its eight back-presses and raised instead. The recovery that would
have worked in two seconds — `mobile: clearApp`, an adb-level call needing no UI at all — was
gated behind the thing that was broken.

So the order is the invariant: when `skip_if_done=False`, nothing may be asked of the app before
its data is erased. There is nothing worth detecting in a wallet that is about to be wiped.
"""
import pytest
from selenium.common.exceptions import NoSuchElementException

from wallets.hovi.flows import init_flow

PKG = "droidwallet.hovi.id"


class BrickedDriver:
    """A crash-looping app: the launcher holds the foreground and nothing is ever findable."""

    def __init__(self):
        self.calls = []

    @property
    def current_package(self):
        return "com.motorola.launcher3"

    def find_element(self, by, value):
        self.calls.append("find_element")
        raise NoSuchElementException("nothing on screen but the launcher")

    def find_elements(self, by, value):
        self.calls.append("find_elements")
        return []

    def execute_script(self, script, args=None):
        self.calls.append(script)

    def terminate_app(self, pkg):
        self.calls.append("terminate_app")

    def activate_app(self, pkg):
        self.calls.append("activate_app")

    def back(self):
        self.calls.append("back")

    @property
    def wiped(self):
        return "mobile: clearApp" in self.calls


def _run(driver, skip_if_done, **extra):
    return init_flow.run(
        driver, pin="123456", skip_if_done=skip_if_done, app_package=PKG,
        timeouts={"default": 0.1}, device_pin="", **extra
    )


@pytest.fixture
def no_slow_probe(monkeypatch):
    """Make state detection instant, and record every time it is consulted."""
    probes = []

    def _detect(driver, timeout=2):
        probes.append(timeout)
        return "unknown"

    monkeypatch.setattr(init_flow, "_detect_state", _detect)
    return probes


def test_reset_wipes_a_wallet_that_cannot_be_driven(no_slow_probe):
    """The regression: a bricked wallet must still be erased."""
    driver = BrickedDriver()
    with pytest.raises(RuntimeError) as exc:
        _run(driver, skip_if_done=False)

    assert driver.wiped, "a reset must clear app data even when the app is unusable"
    # It fails afterwards — the app cannot come up — but it now says so honestly, instead of
    # blaming a state machine that never got the chance to run.
    assert "Landing page not found after reset" in str(exc.value)
    assert "stuck in unknown state" not in str(exc.value)


def test_reset_asks_the_app_nothing_before_erasing_it(no_slow_probe):
    """The invariant. Any probe here is a way for the wipe to become unreachable again."""
    driver = BrickedDriver()
    with pytest.raises(RuntimeError):
        _run(driver, skip_if_done=False)

    assert no_slow_probe == [], "state was detected before the wipe"
    assert "back" not in driver.calls, "back was pressed before the wipe"
    assert driver.calls[0] == "mobile: clearApp", f"wipe was not first: {driver.calls[:4]}"


def test_a_normal_run_never_wipes(no_slow_probe):
    """`skip_if_done=True` keeps the old recovery behaviour, wipe strictly excluded.

    The reset is destructive and once-per-session; a plain navigate-to-home must never trigger it,
    however lost the app is.
    """
    driver = BrickedDriver()
    with pytest.raises(RuntimeError) as exc:
        _run(driver, skip_if_done=True)

    assert not driver.wiped, "a non-reset run wiped the wallet"
    assert driver.calls.count("back") == 8, "the back-press recovery should still run"
    assert "stuck in unknown state" in str(exc.value)


def test_a_normal_run_on_home_is_a_no_op(monkeypatch):
    monkeypatch.setattr(init_flow, "_detect_state", lambda driver, timeout=2: "home")
    driver = BrickedDriver()

    assert _run(driver, skip_if_done=True) is None
    assert not driver.wiped
    assert driver.calls == [], f"nothing should have been driven: {driver.calls}"
