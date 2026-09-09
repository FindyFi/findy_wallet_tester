"""The device PIN is only ever typed when the keyguard is provably up.

On 2026-09-02 Appium decided the test phone was locked when it was not, typed the device PIN, and
the keystrokes landed in a focused Google search box — which submitted the PIN as a web query. The
run reported only "The device has failed to be unlocked"; the leak itself was silent.

The costs are asymmetric, and that is what these tests encode: a missed unlock is a clean timeout,
a mistyped PIN is a secret sent somewhere it cannot be recalled from. So "don't know" must behave
like "don't type".
"""
import pytest

from base import android


class FakeDriver:
    def __init__(self, locked=True):
        self._locked = locked
        self.unlocked_with = None

    def execute_script(self, script, *args):
        if script == "mobile: isLocked":
            return self._locked
        if script == "mobile: unlock":
            self.unlocked_with = args[0] if args else {}
            return None
        raise AssertionError(f"unexpected script {script}")


def _keyguard(monkeypatch, value):
    monkeypatch.setattr(android, "keyguard_showing", lambda serial="": value)


def test_pin_is_typed_when_both_sources_agree_the_keyguard_is_up(monkeypatch):
    _keyguard(monkeypatch, True)
    driver = FakeDriver(locked=True)
    assert android.unlock_if_locked(driver, "1234", "serial") is True
    assert driver.unlocked_with["key"] == "1234"


def test_pin_is_not_typed_when_the_window_manager_disagrees(monkeypatch):
    """The leak case: Appium says locked, the device says otherwise. Do not type."""
    _keyguard(monkeypatch, False)
    driver = FakeDriver(locked=True)
    assert android.unlock_if_locked(driver, "1234", "serial") is False
    assert driver.unlocked_with is None


def test_pin_is_not_typed_when_the_keyguard_state_cannot_be_read(monkeypatch):
    """"Don't know" is not "not locked" — it is "don't type"."""
    _keyguard(monkeypatch, None)
    driver = FakeDriver(locked=True)
    assert android.unlock_if_locked(driver, "1234", "serial") is False
    assert driver.unlocked_with is None


def test_an_unlocked_device_is_left_alone(monkeypatch):
    _keyguard(monkeypatch, True)
    driver = FakeDriver(locked=False)
    assert android.unlock_if_locked(driver, "1234", "serial") is False
    assert driver.unlocked_with is None


def test_no_pin_configured_means_nothing_is_typed(monkeypatch):
    """A device with no lock screen: DEVICE_PIN is blank and must stay unused."""
    _keyguard(monkeypatch, True)
    driver = FakeDriver(locked=True)
    assert android.unlock_if_locked(driver, "", "serial") is False
    assert driver.unlocked_with is None


def test_the_uiautomator_strategy_is_used(monkeypatch):
    """Appium's default 'locksettings' strategy deletes the lock and wipes enrolled fingerprints."""
    _keyguard(monkeypatch, True)
    driver = FakeDriver(locked=True)
    android.unlock_if_locked(driver, "1234", "serial")
    assert driver.unlocked_with["strategy"] == "uiautomator"


def test_a_failing_unlock_does_not_raise(monkeypatch):
    """A device that will not unlock should surface as the wallet not coming forward."""
    _keyguard(monkeypatch, True)

    class Boom(FakeDriver):
        def execute_script(self, script, *args):
            if script == "mobile: isLocked":
                return True
            raise RuntimeError("unlock exploded")

    assert android.unlock_if_locked(Boom(), "1234", "serial") is True


# --- reading the keyguard state off the device -------------------------------------------------

def _dumpsys(monkeypatch, text):
    import subprocess
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: type("R", (), {"stdout": text, "returncode": 0})())


# Real fragments, captured from the moto g24 test phone on 2026-09-02 in both states. Note
# isKeyguardShowing reads `true` in BOTH — it is stuck on this device, and trusting it is what
# leaked the PIN. Only mInputRestricted tells the two apart.
_UNLOCKED = "  isKeyguardShowing=true\n  mInputRestricted=false\n  mDreamingLockscreen=true\n"
_LOCKED = "  isKeyguardShowing=true\n  mInputRestricted=true\n  mDreamingLockscreen=true\n"


def test_unlocked_device_is_reported_unlocked_despite_a_stuck_keyguard_flag(monkeypatch):
    _dumpsys(monkeypatch, _UNLOCKED)
    assert android.keyguard_showing("serial") is False


def test_locked_device_is_reported_locked(monkeypatch):
    _dumpsys(monkeypatch, _LOCKED)
    assert android.keyguard_showing("serial") is True


def test_a_device_without_the_flag_gives_no_opinion(monkeypatch):
    """No safe fallback exists — the other keyguard flags were measured unreliable."""
    _dumpsys(monkeypatch, "  isKeyguardShowing=true\n  mDreamingLockscreen=true\n")
    assert android.keyguard_showing("serial") is None


def test_unreadable_dumpsys_gives_no_opinion(monkeypatch):
    import subprocess
    def boom(*a, **k):
        raise OSError("adb not found")
    monkeypatch.setattr(subprocess, "run", boom)
    assert android.keyguard_showing("serial") is None
