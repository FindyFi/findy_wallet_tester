"""No PIN and no full local path in anything a run writes.

The phone's PIN was in plain text in every failing run's records: pytest's long traceback prints
each frame's arguments, and `page_args = {..., 'device_pin': '<PIN>'}` was one of them (643 times
in one nightly log, in 20 pytest.out files of this repository's reports). Appium's server log,
which we copy into appium.log, carried it in the `mobile: unlock` body, and its keypad clicks
spelled it out. Every record also named this machine's home folder.

Every PIN below is made up. None of these tests prints the registry: the real PINs from `.env`
are registered in this process too, and the `clean_registry` fixture keeps them out of the way.
"""
import copy
import json
import logging
import re
from types import SimpleNamespace

import pytest
from _pytest._code.code import ExceptionInfo
from _pytest.reports import TestReport

from base import redact as r
from base.conftest_helpers import capture_appium_logs, redact_report
from base.tests.test_appium_log_capture import RollingDriver

DEVICE_PIN = "97531"       # shorter than 6: masked only where it is plainly a PIN
APP_PIN = "Wq7319z"        # 6 or longer: masked wherever it stands alone
ROOT = str(r.REPO_ROOT)


@pytest.fixture(autouse=True)
def clean_registry():
    saved = set(r._secrets)
    r.clear_secrets()
    r.register_secret(DEVICE_PIN)
    r.register_secret(APP_PIN)
    yield
    r.clear_secrets()
    r._secrets.update(saved)


# --- by value ----------------------------------------------------------------------------------

@pytest.mark.parametrize("text, expected", [
    ("page_args = {'debug': False, 'device_pin': '97531'}",
     "page_args = {'debug': False, 'device_pin': '****'}"),
    ('POST /element/abc/value {"text":"97531","value":["9","7","5","3","1"]}',
     'POST /element/abc/value {"text":"****","value":["****"]}'),
    ('Calling AppiumDriver.setValue() with args: ["97531","e-1","s-1"]',
     'Calling AppiumDriver.setValue() with args: ["****","e-1","s-1"]'),
    ("typed Wq7319z into the field", "typed **** into the field"),
    ('text="Wq7319z"', 'text="****"'),
])
def test_a_registered_pin_is_masked(text, expected):
    assert r.redact(text) == expected


@pytest.mark.parametrize("text", [
    "emulator-97531 is up",                  # short PIN in free text: left, or the mask names it
    "build 975310 and id x97531",            # inside a longer token
    "XWq7319zX",
    '["9","7","5"]',                          # a different digit list
    '"97530"',
])
def test_text_that_only_resembles_a_pin_is_left_alone(text):
    assert r.redact(text) == text


def test_values_too_short_to_be_a_pin_are_never_registered():
    r.register_secret("p")
    r.register_secret("")
    r.register_secret(None)
    assert r.redact("a 'p' and p") == "a 'p' and p"


# --- by shape ----------------------------------------------------------------------------------

@pytest.mark.parametrize("text, expected", [
    ("device_pin = '24680'", "device_pin = '****'"),
    ("{'device_pin': '24680', 'debug': True}", "{'device_pin': '****', 'debug': True}"),
    ('"pin": "Other99"', '"pin": "****"'),
    ("DEVICE_PIN=24680", "DEVICE_PIN=****"),
    ("HEIDI_APP_PIN: 24680 set", "HEIDI_APP_PIN: **** set"),
    ('{"script":"mobile: unlock","args":[{"key":"24680","type":"pin","strategy":"uiautomator"}]}',
     '{"script":"mobile: unlock","args":[{"key":"****","type":"pin","strategy":"uiautomator"}]}'),
    ("Trying to unlock device using pin 24680", "Trying to unlock device using pin ****"),
])
def test_a_pin_we_were_never_told_about_is_masked_by_its_shape(text, expected):
    assert r.redact(text) == expected


@pytest.mark.parametrize("text", [
    "def unlock_if_locked(driver, pin: str, device_serial: str = ''):",
    "unlock_if_locked(driver, pin=pin)",
    "unlock_if_locked(driver, device_pin=device_pin)",
    "mapping='a1' typing=2 spinner: 3 shipping = '4'",
    "'pin': ''",
    "PIN login failed — check 'pin' in config",
    '{"type":"pin","strategy":"uiautomator"}',
    "timeouts: {'pin_digit_delay': 0.3}",
    "page_args = {'device_pin': '', 'debug': False}",       # an emulator with no screen lock
    "Incorrect PIN: 2 attempts remaining",                    # the wallet's own words
])
def test_code_and_prose_about_pins_is_left_alone(text):
    assert r.redact(text) == text


# --- paths -------------------------------------------------------------------------------------

@pytest.mark.parametrize("text, expected", [
    (f"XML dump saved: {ROOT}/reports/run/heidi/xml_dumps/t.xml",
     "XML dump saved: reports/run/heidi/xml_dumps/t.xml"),
    (f"rootdir: {ROOT}", "rootdir: ."),
    ("Running '/Users/someone/Library/Android/sdk/platform-tools/adb -s X shell'",
     "Running '~/Library/Android/sdk/platform-tools/adb -s X shell'"),
    ("/home/runner/work/x.py:12", "~/work/x.py:12"),
    ("[ADB] /Users/a.b-c/gits/PUBLISH/reports/x", "[ADB] ~/gits/PUBLISH/reports/x"),
    ("Generated html report: file:///Users/someone/gits/PUBLISH/reports/r/report.html",
     "Generated html report: file://~/gits/PUBLISH/reports/r/report.html"),
    ("https://issuer.example/home/alice/offer", "https://issuer.example/home/alice/offer"),
])
def test_local_paths_are_shortened(text, expected):
    assert r.redact(text) == expected


def test_clean_text_comes_back_unchanged_and_redaction_is_idempotent():
    clean = "[verification_flow] hovi rejected 'pension_verification': \"Invalid request\" [no_retry]"
    assert r.redact(clean) == clean
    dirty = f"device_pin = '97531' at {ROOT}/base/x.py"
    once = r.redact(dirty)
    assert once == "device_pin = '****' at base/x.py"
    assert r.redact(once) == once


def test_redact_json_redacts_every_string_and_keeps_the_shape():
    data = {"error": "device_pin = '97531'", "n": 3, "tests": [{"path": f"{ROOT}/a"}, None]}
    assert r.redact_json(data) == {"error": "device_pin = '****'", "n": 3,
                                   "tests": [{"path": "a"}, None]}


# --- Secret and the config ---------------------------------------------------------------------

def test_a_secret_types_like_its_value_and_prints_as_the_mask():
    pin = r.Secret(DEVICE_PIN)
    assert repr(pin) == "'****'"
    assert repr({"device_pin": pin}) == "{'device_pin': '****'}"
    assert pin == DEVICE_PIN and str(pin) == DEVICE_PIN and list(pin) == list(DEVICE_PIN)
    assert json.dumps({"k": pin}) == '{"k": "97531"}'
    assert type(copy.deepcopy(pin)) is r.Secret


def test_mark_secrets_wraps_every_pin_at_any_depth():
    r.clear_secrets()
    config = {"application": {"pin": "Zz11223"}, "android": {"device_pin": "86420"},
              "timeouts": {"pin_digit_delay": 0.3}, "list": [{"app_pin": "Qq9988"}],
              "blank": {"pin": ""}}
    assert r.mark_secrets(config) is config
    assert type(config["application"]["pin"]) is r.Secret
    assert type(config["android"]["device_pin"]) is r.Secret
    assert type(config["list"][0]["app_pin"]) is r.Secret
    assert config["timeouts"]["pin_digit_delay"] == 0.3
    assert config["blank"]["pin"] == ""
    assert r.redact('"86420" and Zz11223 and Qq9988') == '"****" and **** and ****'


def test_the_environment_registers_device_and_app_pins_only():
    r.clear_secrets()
    r.register_environment_secrets({
        "DEVICE_PIN": "11357", "TOPPAN_DEVICE_PIN": "22468", "HEIDI_APP_PIN": "Hh55667",
        "DEVICE_NAME": "emulator-5554", "PIN_DIGIT_DELAY": "0.3333",
    })
    assert r._secrets == {"11357", "22468", "Hh55667"}


def test_load_config_hands_out_pins_as_secrets(monkeypatch):
    from base.config import load_config
    r.clear_secrets()
    monkeypatch.setenv("DEVICE_NAME", "emulator-5554")
    monkeypatch.setenv("DEVICE_PIN", "13579")
    monkeypatch.setenv("EXAMPLE_APP_PIN", "Ex98765")
    monkeypatch.delenv("EXAMPLE_DEVICE_PIN", raising=False)
    config = load_config("example")
    assert type(config["android"]["device_pin"]) is r.Secret
    assert type(config["application"]["pin"]) is r.Secret
    assert repr(config["android"]) .count("13579") == 0
    assert {"13579", "Ex98765"} <= r._secrets


# --- Appium's device unlock --------------------------------------------------------------------

_UNLOCK = [
    '[HTTP] --> POST /session/s1/execute/sync {"script":"mobile: unlock","args":[{"key":"97531"}]}',
    '[Driver] Trying to unlock device using pin 97531',
    '[Driver] Proxying [POST /elements] with body: {"strategy":"id","selector":"com.android.systemui:id/digit_text"}',
    '[Driver] Got response with status 200: {"value":"9"}',
    '[Driver] Matched \'/element/e9/click\' to command name \'click\'',
    '[HTTP] <-- POST /session/s1/execute/sync 200 2117 ms - 14',
]


def test_the_whole_unlock_exchange_is_left_out():
    lines = ["[HTTP] --> GET /status"] + _UNLOCK + ["[HTTP] --> POST /session/s1/element"]
    assert r.drop_unlock_exchanges(lines) == [
        "[HTTP] --> GET /status",
        "[redacted] 6 Appium log lines of the device unlock",
        "[HTTP] --> POST /session/s1/element",
    ]


def test_an_unlock_cut_off_by_the_log_window_is_still_left_out():
    # get_log('server') returns a rolling window: it can start after the request line.
    assert r.drop_unlock_exchanges(_UNLOCK[2:] + ["after"]) == [
        "[redacted] 4 Appium log lines of the device unlock", "after"]
    # ...or end before the answer: everything after the start goes.
    assert r.drop_unlock_exchanges(["before"] + _UNLOCK[:4]) == [
        "before", "[redacted] 4 Appium log lines of the device unlock"]


def test_appium_log_holds_neither_the_unlock_nor_a_typed_pin_nor_local_paths(tmp_path):
    driver = RollingDriver(window=100)
    for i, message in enumerate(_UNLOCK + [
        '[HTTP] --> POST /session/s1/element/e1/value {"text":"Wq7319z","value":["W","q","7","3","1","9","z"]}',
        "[ADB] Running '/Users/someone/Library/Android/sdk/platform-tools/adb -s X shell input keyevent 66'",
    ]):
        driver.emit(1000 + i, message)
    request = SimpleNamespace(config=SimpleNamespace(_run_dir=tmp_path, _session_started_at=0),
                              node=SimpleNamespace(name="test_x", nodeid="test_x"))
    capture_appium_logs(driver, request, "test_x")
    text = (tmp_path / "appium.log").read_text()
    body = [re.sub(r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d \[INFO\] ", "", line)
            for line in text.splitlines()[2:]]
    assert body == [
        "[redacted] 6 Appium log lines of the device unlock",
        '[HTTP] --> POST /session/s1/element/e1/value {"text":"****","value":["****"]}',
        "[ADB] Running '~/Library/Android/sdk/platform-tools/adb -s X shell input keyevent 66'",
    ]


def test_a_pin_typed_on_a_wallet_keypad_is_left_out_of_appium_log(tmp_path, monkeypatch):
    # The digit lookups name each key ("quick_pin_digit_7"), so in order they spell the app PIN.
    r.clear_secret_windows()
    clock = iter([1.0, 2.0])
    monkeypatch.setattr(r.time, "time", lambda: next(clock))
    with r.typing_secret():
        pass
    assert r._secret_windows == [(950.0, 2050.0)]
    driver = RollingDriver(window=100)
    driver.emit(940, "[HTTP] --> GET /before")
    driver.emit(1000, '[HTTP] --> POST /session/s1/element {"using":"id","value":"x:id/quick_pin_digit_7"}')
    driver.emit(2000, '[HTTP] --> POST /session/s1/element {"using":"id","value":"x:id/quick_pin_digit_3"}')
    driver.emit(2060, "[HTTP] --> GET /after")
    request = SimpleNamespace(config=SimpleNamespace(_run_dir=tmp_path, _session_started_at=0),
                              node=SimpleNamespace(name="test_x", nodeid="test_x"))
    try:
        capture_appium_logs(driver, request, "test_x")
    finally:
        r.clear_secret_windows()
    body = [re.sub(r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d \[INFO\] ", "", line)
            for line in (tmp_path / "appium.log").read_text().splitlines()[2:]]
    assert body == ["[HTTP] --> GET /before", "[redacted] 2 Appium log lines of PIN entry",
                    "[HTTP] --> GET /after"]


def test_every_keypad_pin_page_types_inside_a_secret_window():
    import inspect
    from wallets.authbound.pages import pin_page as authbound
    from wallets.example.pages import pin_page as example
    from wallets.hovi.pages import pin_page as hovi
    from wallets.paradym.pages import pin_page as paradym
    from wallets.procivis.pages import pin_page as procivis
    for page in (authbound.PinPage.enter_pin, example.PinPage.enter_pin, hovi.PinPage.enter,
                 paradym.PinPage.enter_pin, procivis.PinPage.enter_pin):
        source = inspect.getsource(page)
        assert "with typing_secret():" in source.split("for digit in pin:")[0], page.__qualname__


# --- logging -----------------------------------------------------------------------------------

@pytest.fixture
def captured():
    """A logger whose records go through a fresh redacting factory to an in-memory handler."""
    previous = logging.getLogRecordFactory()
    logging.setLogRecordFactory(logging.LogRecord)
    r.install_log_redaction()
    seen = []

    class Keep(logging.Handler):
        def emit(self, record):
            seen.append(self.format(record))

    handler = Keep()
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger = logging.getLogger("test_redact.captured")
    logger.addHandler(handler)
    logger.propagate = False
    logger.setLevel(logging.DEBUG)
    yield logger, seen
    logger.removeHandler(handler)
    logging.setLogRecordFactory(previous)


def test_every_log_record_is_redacted_before_any_handler_sees_it(captured):
    logger, seen = captured
    logger.info("Screenshot saved: %s/reports/x.png, pin %s", ROOT, "x")
    logger.info(f"unlocking with {{'device_pin': '{DEVICE_PIN}'}}")
    assert seen == ["Screenshot saved: reports/x.png, pin x", "unlocking with {'device_pin': '****'}"]


def test_a_logged_traceback_is_redacted(captured):
    logger, seen = captured

    def unlock(device_pin):
        raise RuntimeError(f"could not unlock with device_pin={device_pin}")

    try:
        unlock(DEVICE_PIN)
    except RuntimeError:
        logger.exception("failed")
    assert DEVICE_PIN not in seen[0]
    assert "RuntimeError: could not unlock with device_pin=****" in seen[0]
    assert ROOT not in seen[0]


def test_a_message_that_cannot_be_formatted_never_shows_its_arguments(captured, capsys):
    logger, seen = captured
    logger.info("two %s %s", APP_PIN)
    assert seen == ["two %s %s [unformattable log message]"]
    assert APP_PIN not in capsys.readouterr().err


def test_the_root_conftest_installs_the_redacting_factory():
    # This session runs under the root conftest, like every wallet run: test.log's FileHandler
    # sees only what the factory lets through.
    assert getattr(logging.getLogRecordFactory(), r._INSTALLED, False)


def test_the_factory_is_installed_once(captured):
    factory = logging.getLogRecordFactory()
    r.install_log_redaction()
    assert logging.getLogRecordFactory() is factory


# --- pytest's reports --------------------------------------------------------------------------

def _failed_report(sections=()):
    def unlock_if_locked(driver, device_pin, page_args):
        raise RuntimeError(f"Unlock failed at {ROOT}/base/android.py")

    try:
        unlock_if_locked(None, DEVICE_PIN, {"device_pin": DEVICE_PIN, "debug": False})
    except RuntimeError:
        excinfo = ExceptionInfo.from_current()
    longrepr = excinfo.getrepr(style="long", funcargs=True)
    return TestReport("t.py::test_x", ("t.py", 1, "test_x"), {}, "failed", longrepr, "call",
                      sections=list(sections))


def test_a_failure_traceback_no_longer_prints_the_pin_it_was_called_with():
    rep = _failed_report()
    assert f"device_pin = '{DEVICE_PIN}'" in rep.longreprtext     # the leak, before
    redact_report(rep)
    text = rep.longreprtext
    assert DEVICE_PIN not in text and ROOT not in text
    assert "device_pin = '****'" in text
    assert "page_args = {'debug': False, 'device_pin': '****'}" in text
    assert "RuntimeError: Unlock failed at base/android.py" in text
    # pytest's short summary line reads the crash message.
    assert rep.longrepr.reprcrash.message == "RuntimeError: Unlock failed at base/android.py"


def test_captured_output_in_a_report_is_redacted():
    rep = _failed_report(sections=[("Captured stdout call", f"typed {APP_PIN} in {ROOT}/x\n")])
    redact_report(rep)
    assert rep.sections == [("Captured stdout call", "typed **** in x\n")]


def test_a_skip_keeps_the_tuple_pytest_and_the_summary_rely_on():
    rep = TestReport("t.py::test_x", ("t.py", 1, "test_x"), {}, "skipped",
                     (f"{ROOT}/t.py", 3, "Skipped: provider unreachable"), "setup")
    redact_report(rep)
    assert rep.longrepr == ("t.py", 3, "Skipped: provider unreachable")


def test_a_redacted_traceback_carries_no_colour_codes_even_when_colour_is_forced(monkeypatch):
    # report.html stores the text; the report generator finds "E   ..." lines by their start.
    monkeypatch.setenv("FORCE_COLOR", "1")
    rep = _failed_report()
    redact_report(rep)
    assert "\x1b[" not in rep.longreprtext
    assert "E       RuntimeError: Unlock failed at base/android.py" in rep.longreprtext


def test_the_terminal_output_is_redacted_once():
    import io
    from _pytest._io import TerminalWriter
    from base.conftest_helpers import redact_terminal
    out = io.StringIO()
    writer = TerminalWriter(out)
    config = SimpleNamespace(get_terminal_writer=lambda: writer)
    redact_terminal(config)
    redact_terminal(config)
    writer.line(f"rootdir: {ROOT}")
    writer.sep("-", f"Generated html report: file://{ROOT}/reports/r/report.html")
    writer.line("page_args = {'device_pin': '97531'}")
    lines = out.getvalue().splitlines()
    assert lines[0] == "rootdir: ."
    assert lines[1].strip("- ") == "Generated html report: file://reports/r/report.html"
    assert lines[2] == "page_args = {'device_pin': '****'}"


def test_a_real_pytest_session_prints_no_pin_and_no_local_path(tmp_path):
    """The root conftest wired in: logging, test reports, warnings, pytest-html's last line."""
    import os
    import subprocess
    import sys
    from pathlib import Path
    home = str(Path.home())
    (tmp_path / "test_leaky.py").write_text(
        "import logging, os, warnings\n"
        "log = logging.getLogger('leaky')\n"
        "def unlock(driver, device_pin, page_args):\n"
        f"    log.info('saved %s/reports/x.png with %s', {home!r}, page_args)\n"
        f"    warnings.warn_explicit('w', UserWarning, {home!r} + '/elsewhere/mod.py', 3)\n"
        f"    raise RuntimeError('unlock failed at ' + {home!r} + '/x')\n"
        "def test_leaky():\n"
        "    log.info('typed %s on the keypad', os.environ['ZZTEST_APP_PIN'])\n"
        "    pin = os.environ['ZZTEST_DEVICE_PIN']\n"
        "    unlock(None, pin, {'device_pin': pin})\n")
    # Loaded with -p, the conftest imports urllib3 before pytest's warning capture starts, and
    # urllib3's import-time warning names its install path; a real run never prints it.
    env = dict(os.environ, ZZTEST_DEVICE_PIN="86420", ZZTEST_APP_PIN="Zq8642x",
               PYTHONWARNINGS="ignore:urllib3 v2 only supports OpenSSL")
    env.pop("PYTEST_SESSION_DIR", None)
    env.pop("PYTEST_RUN_DIR", None)
    run = subprocess.run(
        [sys.executable, "-m", "pytest", str(tmp_path / "test_leaky.py"), "-p", "conftest",
         "-p", "no:cacheprovider", "--rootdir", str(tmp_path), "-o", "log_cli=true", "--log-cli-level=INFO",
         "--html", str(tmp_path / "report.html"), "--reruns", "0"],
        cwd=str(r.REPO_ROOT), env=env, capture_output=True, text=True, timeout=120)
    out = run.stdout + run.stderr
    html = (tmp_path / "report.html").read_text()
    assert run.returncode == 1, "the leaky test should have run and failed"
    for secret in ("86420", "Zq8642x", home):
        assert secret not in out and secret not in html
    assert "typed **** on the keypad" in out
    assert "page_args = {'device_pin': '****'}" in out
    assert "Generated html report: file://" in out
    assert "UserWarning: w" in out


def test_a_passed_report_is_left_as_it_is():
    rep = TestReport("t.py::test_x", ("t.py", 1, "test_x"), {}, "passed", None, "call")
    redact_report(rep)
    assert rep.longrepr is None and rep.sections == []


# --- the published report ----------------------------------------------------------------------

def test_the_report_generator_cannot_publish_a_pin_or_a_path_from_an_old_run(tmp_path):
    import generate_compact_report as g
    r.clear_secrets()      # the generator never loads .env: it relies on shapes and paths
    log = (f"E   RuntimeError: [setup] page_args = {{'device_pin': '24680'}} at "
           f"/Users/someone/gits/x/reports/r.png")
    category, detail = g.classify_failure(log)
    assert "24680" not in detail and "/Users/" not in detail
    (tmp_path / "summary.json").write_text(json.dumps({"tests": [
        {"nodeid": "n", "error": "FlowFailure: device_pin = '24680'", "category": "setup"}]}))
    assert g.load_summary(tmp_path)["n"]["error"] == "FlowFailure: device_pin = '****'"
    (tmp_path / "app_info.json").write_text(json.dumps(
        {"update_check_error": f"failed at {ROOT}/base/x.py"}))
    assert g.load_app_info(tmp_path) == {"update_check_error": "failed at base/x.py"}
