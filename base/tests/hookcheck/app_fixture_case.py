"""The real `app` fixture against a fake driver, run as a child session by test_app_fixture_fallback.py.

Not named `test_*.py`, so the real suite never collects it: several of these tests fail on purpose.
It lives inside the repo for the same reason as `failing_case.py` — only a child session started
here loads the root `conftest.py`, and a copy of the fixture would prove nothing about the real one.

The driver is the only thing faked, plus the handful of module globals the fixture reaches for
before it can yield (config, install, unlock, recents). Everything that decides *whether and when*
a capture happens — the fixture, its finalizer, the makereport hook, `save_failure_artifacts`,
`capture_appium_logs` — is the real code.

Each test's driver records the calls made on it, and writes them out at driver teardown. That
teardown runs after the `app` fixture's own teardown and finalizers, because `app` depends on
`driver`, so the record is complete when it is written.
"""
import base64
import json
import os
import time
from pathlib import Path

import pytest
from selenium.common.exceptions import WebDriverException

import conftest as root
from base.conftest_helpers import capture_appium_logs, capture_failure_artifact

OUT_DIR = "APPCHECK_OUT"
WALLET = "fakewallet"
PACKAGE = "com.example.fakewallet"

# Attempts per test id, across reruns. pytest-rerunfailures reuses the item, not the module.
_ATTEMPTS = {}
_LOG_SEQ = [0]


class FakeDriver:
    """An Appium driver that records calls. `dies` makes `activate_app` raise, as unime's did."""

    capabilities = {"udid": "fake-serial", "deviceName": "fake-serial", "platformName": "Android",
                    "platformVersion": "14", "deviceModel": "fake"}

    def __init__(self, calls, *, dies=False, captures_raise=False, attempt=1):
        self.calls = calls
        self.attempt = attempt  # stamped into every file, so a test can tell whose it is
        self.dies = dies
        self.captures_raise = captures_raise

    def _capture(self, name):
        self.calls.append(name)
        if self.captures_raise:
            raise WebDriverException(f"{name}: the session is gone")

    def start_recording_screen(self, **kwargs):
        self.calls.append("start_recording_screen")

    def stop_recording_screen(self):
        self._capture("stop_recording_screen")
        return base64.b64encode(f"mp4 attempt={self.attempt}".encode()).decode()

    def press_keycode(self, code):
        self.calls.append(f"press_keycode:{code}")

    def activate_app(self, package):
        self.calls.append("activate_app")
        if self.dies:
            # The shape unime's 33 failed attempts took on 2026-09-10.
            raise WebDriverException("An unknown server-side error occurred while processing the "
                                     "command. instrumentation process is not running")

    @property
    def current_package(self):
        return PACKAGE

    @property
    def page_source(self):
        self._capture("page_source")
        return f"<hierarchy attempt='{self.attempt}'><node text='fake'/></hierarchy>"

    def save_screenshot(self, path):
        self._capture("save_screenshot")
        Path(path).write_bytes(f"png attempt={self.attempt}".encode())
        return True

    def get_log(self, kind):
        self._capture(f"get_log:{kind}")
        # Unique across the whole session, as a real server's entries are. `capture_appium_logs`
        # dedups on (timestamp, message), so two tests capturing in the same millisecond with a
        # per-driver counter would each emit "entry 1", and the second would rightly be dropped.
        _LOG_SEQ[0] += 1
        return [{"timestamp": int(time.time() * 1000), "level": "INFO",
                 "message": f"appium entry {_LOG_SEQ[0]}"}]


class _FakeBaseTest:
    """What the `app` fixture yields. `driver` and `config` are what wallet teardowns read off it."""

    def __init__(self, driver, config):
        self.driver = driver
        self.config = config

    def setup(self):
        return False  # "already installed"


# What each test's driver does on each attempt, by test function name; the last entry repeats.
# A lookup rather than custom markers, which a test module cannot register and which would warn —
# or error under `--strict-markers`.
_MODES = {
    "test_dies_before_yield": ["dies"],
    "test_rerun_then_dies": ["normal", "dies"],
    "test_fails_then_passes": ["normal"],
    "test_fallback_with_a_raising_driver": ["dies_and_captures_raise"],
}


def _mode(request):
    modes = _MODES.get(request.node.originalname, ["normal"])
    return modes[min(_ATTEMPTS[request.node.nodeid], len(modes)) - 1]


@pytest.fixture
def driver(request, monkeypatch):
    """Overrides the root `driver`: no Appium server, no device."""
    _ATTEMPTS[request.node.nodeid] = _ATTEMPTS.get(request.node.nodeid, 0) + 1
    mode = _mode(request)

    monkeypatch.setattr(root, "load_config", lambda name: {
        "application": {"package": PACKAGE, "pin": ""},
        "recording": {"enabled": True},
        "updates": {"enabled": False},
        "timeouts": {"default": 1},
        "reporting": {"xml_on_failure": True, "screenshot_on_failure": True},
    })
    monkeypatch.setattr(root, "BaseTest", _FakeBaseTest)
    monkeypatch.setattr(root, "unlock_if_locked", lambda *a, **k: None)
    monkeypatch.setattr(root, "_clear_recent_apps", lambda d: d.calls.append("clear_recents"))
    monkeypatch.setattr(root, "_is_anr_present", lambda d: False)
    monkeypatch.setattr(root, "handle_permission_if_present", lambda *a, **k: False)
    monkeypatch.setattr(root, "get_app_info", lambda package, serial: {
        "package": package, "version_name": "1.0", "version_code": "1"})

    calls = []
    fake = FakeDriver(calls, dies=mode in ("dies", "dies_and_captures_raise"),
                      captures_raise=mode == "dies_and_captures_raise",
                      attempt=_ATTEMPTS[request.node.nodeid])

    if mode == "dies_and_captures_raise":
        # The real helpers already swallow driver errors, so a raising driver alone never reaches
        # the fallback's own guards. Make the helpers themselves raise, as a bug in one would.
        def _boom(name):
            def raiser(*a, **k):
                calls.append(name)
                raise RuntimeError(f"{name} blew up")
            return raiser
        monkeypatch.setattr(root, "save_failure_artifacts", _boom("save_failure_artifacts"))
        monkeypatch.setattr(root, "capture_appium_logs", _boom("capture_appium_logs"))
    yield fake

    out = Path(os.environ[OUT_DIR])
    name = request.node.name
    attempt = _ATTEMPTS[request.node.nodeid]
    (out / f"{name}.{attempt}.json").write_text(json.dumps({
        "run_dir": str(request.config._run_dir),
        "test_name": root.sanitize_test_name(name),
        "mode": mode,
        "calls": calls,
    }))


@pytest.fixture
def wallet_teardown(app, request):
    """The shape of every wallet's `_ensure_home`: a dependent fixture whose teardown captures
    first, through the same helpers `teardown_test` calls, and sets the flags itself."""
    yield
    capture_failure_artifact(app, request)
    capture_appium_logs(app.driver, request, root.sanitize_test_name(request.node.name))
    request.node._appium_captured = True


@pytest.mark.parametrize("driver", [WALLET], indirect=True)
def test_dies_before_yield(app):
    pytest.fail("unreachable: the fixture raises before yielding")


@pytest.mark.parametrize("driver", [WALLET], indirect=True)
def test_fails_in_body(app):
    raise AssertionError("the wallet did something wrong")


@pytest.mark.parametrize("driver", [WALLET], indirect=True)
def test_passes(app):
    pass


# Attempt 1 gets through setup and fails in the body, so its teardown sets every capture flag.
# Attempt 2 dies before yielding. Its captures must not be skipped on attempt 1's flags.
@pytest.mark.flaky(reruns=1)
@pytest.mark.parametrize("driver", [WALLET], indirect=True)
def test_rerun_then_dies(app):
    raise AssertionError("attempt 1 fails in the body")


# Fails on attempt 1 only. Its failure files must be kept as `.attempt1.*` once the rerun starts.
@pytest.mark.flaky(reruns=1)
@pytest.mark.parametrize("driver", [WALLET], indirect=True)
def test_fails_then_passes(app, request):
    if _ATTEMPTS[request.node.nodeid] == 1:
        raise AssertionError("attempt 1 fails in the body")


@pytest.mark.parametrize("driver", [WALLET], indirect=True)
def test_fallback_with_a_raising_driver(app):
    pytest.fail("unreachable: the fixture raises before yielding")


@pytest.mark.parametrize("driver", [WALLET], indirect=True)
def test_fails_behind_a_wallet_teardown(wallet_teardown):
    raise AssertionError("the wallet did something wrong")
