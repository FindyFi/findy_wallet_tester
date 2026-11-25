"""authenticate_device / device_auth_prompt: one detection, then branch on what the sheet shows.

The failure these pin down (gataca, emulator-5554, 2026-11-05): a fingerprint-only system sheet
met the PIN path, which found no "Use PIN" and raised. The fix detects once and never goes on to
the PIN from the fingerprint branch.
"""
import re
from pathlib import Path
from types import SimpleNamespace

from base import android, interstitials

ROOT = Path(__file__).resolve().parents[2]


class Rig:
    """Fakes the three things authenticate_device consults."""

    def __init__(self, monkeypatch, *, sheet=True, icon=True, emulator=True, handled=True):
        self.waits = []
        self.fingerprint_calls = []
        self.pin_calls = []
        self.sheet, self.icon, self.emulator, self.handled = sheet, icon, emulator, handled

        def wait_present(driver, locator, timeout=2):
            self.waits.append((locator, timeout))
            if locator == android._AUTH_PROMPT:
                return self.sheet
            if locator == android.BIOMETRIC_PROMPT:
                return self.icon
            return False

        def handle_biometric(driver, dismiss_timeout=10, detect_timeout=2):
            self.fingerprint_calls.append((dismiss_timeout, detect_timeout))
            return self.handled

        def with_pin(driver, pin, detect_timeout=2, dismiss_timeout=10):
            self.pin_calls.append((pin, detect_timeout, dismiss_timeout))
            return True

        monkeypatch.setattr(android, "wait_present", wait_present)
        monkeypatch.setattr(android, "fingerprint_emulation_available", lambda d: self.emulator)
        monkeypatch.setattr(android, "handle_biometric_if_present", handle_biometric)
        monkeypatch.setattr(android, "authenticate_with_pin", with_pin)


def test_emulator_fingerprint_sheet_injects_once_and_never_types_the_pin(monkeypatch):
    rig = Rig(monkeypatch)
    assert android.authenticate_device(object(), "1234", dismiss_timeout=7) is True
    assert rig.fingerprint_calls == [(7, 0.5)]
    assert rig.pin_calls == []


def test_fingerprint_branch_reporting_not_handled_does_not_fall_back_to_the_pin(monkeypatch):
    rig = Rig(monkeypatch, handled=False)
    assert android.authenticate_device(object(), "1234") is False
    assert rig.pin_calls == []


def test_phone_uses_the_pin_and_never_injects_a_fingerprint(monkeypatch):
    rig = Rig(monkeypatch, emulator=False)
    assert android.authenticate_device(object(), "1234", dismiss_timeout=7) is True
    assert rig.fingerprint_calls == []
    assert rig.pin_calls == [("1234", 0.5, 7)]


def test_emulator_pin_only_view_uses_the_pin(monkeypatch):
    rig = Rig(monkeypatch, icon=False)
    assert android.authenticate_device(object(), "1234") is True
    assert rig.fingerprint_calls == []
    assert len(rig.pin_calls) == 1


def test_no_sheet_does_nothing_and_detects_only_once(monkeypatch):
    rig = Rig(monkeypatch, sheet=False)
    assert android.authenticate_device(object(), "1234", detect_timeout=0.4) is False
    assert rig.fingerprint_calls == [] and rig.pin_calls == []
    assert rig.waits == [(android._AUTH_PROMPT, 0.4)]


def _ctx():
    return SimpleNamespace(device_pin="9876", notes=set(), flow="f", wallet="gataca")


def test_device_auth_prompt_passes_the_context_pin(monkeypatch):
    seen = []
    monkeypatch.setattr(interstitials, "authenticate_device",
                        lambda d, pin, detect_timeout=2: seen.append((pin, detect_timeout)) or True)
    monkeypatch.setattr(interstitials, "in_biometric_enrollment", lambda d: False)
    item = interstitials.device_auth_prompt()
    assert item.name == "device-auth" and item.max_fires == 4 and item.extends == 10.0
    assert item.handle(object(), _ctx()) is True
    assert seen == [("9876", 0.4)]


def test_device_auth_prompt_absorbs_the_enrollment_wizard(monkeypatch):
    dismissed = []

    def boom(d, pin, detect_timeout=2):
        raise TimeoutError("still on the wizard")

    monkeypatch.setattr(interstitials, "authenticate_device", boom)
    monkeypatch.setattr(interstitials, "in_biometric_enrollment", lambda d: True)
    monkeypatch.setattr(interstitials, "dismiss_biometric_enrollment",
                        lambda d: dismissed.append(1))
    ctx = _ctx()
    assert interstitials.device_auth_prompt().handle(object(), ctx) is True
    assert dismissed and interstitials.ENROLLMENT_OFFERED in ctx.notes


def test_device_pin_prompt_is_unchanged(monkeypatch):
    seen = []
    monkeypatch.setattr(interstitials, "authenticate_with_pin",
                        lambda d, pin, detect_timeout=2: seen.append((pin, detect_timeout)) or True)
    monkeypatch.setattr(interstitials, "in_biometric_enrollment", lambda d: False)
    item = interstitials.device_pin_prompt()
    assert item.name == "device-pin" and item.max_fires == 4 and item.extends == 10.0
    assert item.handle(object(), _ctx()) is True and seen == [("9876", 0.4)]


def test_no_gataca_flow_answers_the_prompt_with_the_pin_alone():
    flows = sorted((ROOT / "wallets/gataca/flows").glob("*.py"))
    assert flows
    for path in flows:
        text = path.read_text()
        assert not re.search(r"authenticate_with_pin|device_pin_prompt", text), path.name
    assert "device_auth_prompt" in (ROOT / "wallets/gataca/flows/cleanup_flow.py").read_text()
    init = (ROOT / "wallets/gataca/flows/init_flow.py").read_text()
    assert "authenticate_device(driver, device_pin, detect_timeout=default_timeout)" in init


def test_a_missing_pin_field_is_a_typed_runtime_error_with_the_same_text(monkeypatch):
    """A fingerprint-only sheet on a phone: callers match the type, not the prose, and callers
    that catch RuntimeError are unaffected."""
    assert issubclass(android.PinFieldMissing, RuntimeError)

    class Driver:
        pass

    monkeypatch.setattr(android, "wait_present",
                        lambda d, locator, timeout=2: locator == android._AUTH_PROMPT)
    try:
        android.authenticate_with_pin(Driver(), "1234")
    except android.PinFieldMissing as exc:
        assert str(exc) == "[android] PIN entry field did not appear after switching to PIN"
    else:
        raise AssertionError("authenticate_with_pin should have raised")
