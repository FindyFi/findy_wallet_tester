"""The device log reaches the right device, or it is not written at all.

This capture was silently dead from at least 2026-06-01 to 2026-09-10 and nothing noticed, so the
tests here are aimed at that failure mode rather than at the happy path. Three things conspired:
`adb logcat` ran with no `-s <serial>` and adb refuses outright when more than one device is
attached (heidi needs the emulator, so there always are); `stderr` went to `DEVNULL`, discarding
the refusal; and `Popen` succeeds regardless, so the `except` never fired and nothing looked at the
file afterwards. Every run carried a 0-byte `app.log` that looked like a log.

The one occasion it did produce bytes was worse: heidi's 374 KB was the **phone's** log, captured
while heidi was testing on the emulator. So "wrong device" is the case that has to fail loudly and
"unknown device" the case that has to write nothing — a log filed under the wrong device is worse
than no log, because someone will read it.
"""
import subprocess
from types import SimpleNamespace

import pytest

import conftest as root_conftest
from conftest import _app_uid, _logcat_command, _logcat_target, start_logcat

PKG = "droidwallet.hovi.id"


class FakeCompleted:
    def __init__(self, stdout=""):
        self.stdout = stdout
        self.returncode = 0


@pytest.fixture
def spy(monkeypatch):
    """Records the adb calls `start_logcat` makes, without running any of them."""
    calls = SimpleNamespace(run=[], popen=[], proc=object())

    def _run(argv, **kwargs):
        calls.run.append(argv)
        return FakeCompleted()

    def _popen(argv, **kwargs):
        calls.popen.append((argv, kwargs))
        return calls.proc

    monkeypatch.setattr(root_conftest.subprocess, "run", _run)
    monkeypatch.setattr(root_conftest.subprocess, "Popen", _popen)
    return calls


def _config(wallet="hovi"):
    """Enough of a pytest config for `_detect_wallet_name` to find the wallet."""
    return SimpleNamespace(args=[f"wallets/{wallet}/tests/test_credential_issuance.py"])


# --- the serial: the whole reason this was broken ---------------------------------------------

def _fixed_device(monkeypatch, serial, package=PKG):
    """Pin the resolved device, so these tests do not depend on the operator's live `.env`.

    They read the real config once and broke the moment `.env` was pointed at an emulator for an
    unrelated survey — a unit test asserting a serial has no business consulting operator config.
    `_logcat_target`'s own reading of that config is covered separately, with `load_config` stubbed.
    """
    monkeypatch.setattr(root_conftest, "_logcat_target", lambda name: (serial, package))


def test_every_adb_call_names_the_device(spy, tmp_path, monkeypatch):
    """No `-s` is the original bug. Both calls need it, not just the streaming one."""
    _fixed_device(monkeypatch, "ZT322L348J")
    monkeypatch.setattr(root_conftest, "_app_uid", lambda serial, package: "10351")
    start_logcat(_config("hovi"), tmp_path)

    assert ["adb", "-s", "ZT322L348J", "logcat", "-c"] in spy.run
    assert all("-s" in c and "ZT322L348J" in c for c in spy.run), \
        f"an adb call went to the default device: {spy.run}"
    argv = spy.popen[0][0]
    assert argv[:4] == ["adb", "-s", "ZT322L348J", "logcat"]


def test_a_wallet_on_the_emulator_is_captured_from_the_emulator(spy, tmp_path, monkeypatch):
    """heidi's 374 KB was the phone's log, captured while it tested on the emulator."""
    _fixed_device(monkeypatch, "emulator-5554", "ch.ubique.heidi.android")
    monkeypatch.setattr(root_conftest, "_app_uid", lambda serial, package: "10198")
    start_logcat(_config("heidi"), tmp_path)

    assert "emulator-5554" in spy.popen[0][0]
    assert "ZT322L348J" not in spy.popen[0][0]


def test_the_serial_comes_from_the_wallets_own_config(monkeypatch):
    """The mechanism the two tests above pin down, with config stubbed rather than read."""
    monkeypatch.setattr(root_conftest, "load_config", lambda name: {
        "android": {"device_name": "emulator-5554"},
        "application": {"package": "ch.ubique.heidi.android"},
    })
    assert _logcat_target("heidi") == ("emulator-5554", "ch.ubique.heidi.android")

    monkeypatch.setattr(root_conftest, "load_config", lambda name: {
        "android": {"device_name": "friendly-label", "udid": "ZT322L348J"},
        "application": {"package": PKG},
    })
    assert _logcat_target("hovi") == ("ZT322L348J", PKG), "an explicit udid must win"


def test_the_refusal_is_kept_not_discarded(spy, tmp_path, monkeypatch):
    """`stderr=DEVNULL` is what hid the failure. It must land in the file."""
    _fixed_device(monkeypatch, "ZT322L348J")
    monkeypatch.setattr(root_conftest, "_app_uid", lambda serial, package: "10351")
    start_logcat(_config("hovi"), tmp_path)

    kwargs = spy.popen[0][1]
    assert kwargs["stderr"] == subprocess.STDOUT
    assert kwargs["stderr"] != subprocess.DEVNULL


def test_nothing_is_captured_when_the_device_is_unknown(spy, tmp_path, monkeypatch):
    monkeypatch.setattr(root_conftest, "_logcat_target", lambda name: ("", ""))
    config = _config("hovi")
    start_logcat(config, tmp_path)

    assert spy.run == [] and spy.popen == []
    assert not (tmp_path / "logcat.log").exists(), \
        "an empty file here is indistinguishable from a quiet device"
    assert not hasattr(config, "_logcat_proc")


def test_a_multi_device_run_captures_nothing_rather_than_one_devices_log(monkeypatch):
    """Publishing devices[0]'s log for a parallel run would misattribute most of it."""
    monkeypatch.setattr(root_conftest, "load_config", lambda name: {
        "android": {"devices": [{"device_name": "emulator-5554"},
                                {"device_name": "emulator-5556"}]},
        "application": {"package": PKG},
    })
    assert _logcat_target("hovi") == ("", "")


def test_an_unreadable_config_captures_nothing(monkeypatch):
    def _boom(name):
        raise RuntimeError("Unset environment variable(s) referenced by the hovi config")

    monkeypatch.setattr(root_conftest, "load_config", _boom)
    assert _logcat_target("hovi") == ("", "")


def test_a_failing_adb_does_not_break_the_session(tmp_path, monkeypatch):
    """The capture is diagnostics. It must never be the reason a run does not start."""
    monkeypatch.setattr(root_conftest, "_logcat_target", lambda name: ("ZT322L348J", PKG))
    monkeypatch.setattr(root_conftest.subprocess, "run", lambda *a, **k: FakeCompleted())

    def _popen(*a, **k):
        raise FileNotFoundError("adb: command not found")

    monkeypatch.setattr(root_conftest.subprocess, "Popen", _popen)
    start_logcat(_config("hovi"), tmp_path)  # must not raise


# --- the uid: which lines are "relevant" ------------------------------------------------------

def test_the_command_scopes_to_the_app_and_the_system(monkeypatch):
    """All priorities of both, because a priority floor drops the evidence — see the module doc
    of the digest tests and `_logcat_command`'s own docstring for the measured numbers."""
    monkeypatch.setattr(root_conftest, "_app_uid", lambda serial, package: "10351")
    argv = _logcat_command("ZT322L348J", PKG)

    assert "--uid=10351,1000" in argv
    assert not [a for a in argv if a.endswith(":W")], \
        "a priority floor would drop unime's D/I logs and authbound's D-level HTTP traffic"
    # The denylist is two tags, both recorded in full elsewhere, and `*:V` keeps everything else.
    assert argv[-1] == "*:V", "the default must stay verbose or the denylist becomes an allowlist"
    silenced = {a for a in argv if a.endswith(":S")}
    assert silenced == {"Finsky:S", "appium:S"}, (
        "every added tag removes evidence; Finsky is Play Store install machinery narrated in "
        f"test.log and appium is appium.log's own subject. Got: {silenced}"
    )


def test_an_uninstalled_app_falls_back_to_the_whole_device_log(monkeypatch):
    """Before `test_install` there is no uid. Too much log is recoverable; too little is not."""
    monkeypatch.setattr(root_conftest, "_app_uid", lambda serial, package: "")
    argv = _logcat_command("ZT322L348J", PKG)

    assert argv[:6] == ["adb", "-s", "ZT322L348J", "logcat", "-v", "time"]
    assert not [a for a in argv if a.startswith("--uid")]


def test_the_uid_is_matched_exactly_because_pm_list_matches_on_substring(monkeypatch):
    """toppan ships walletapp *and* superapp; the wrong uid captures the wrong app's log."""
    listing = ("package:com.toppansecurity.superapp uid:10241\n"
               "package:com.toppansecurity.walletapp uid:10291\n")
    monkeypatch.setattr(root_conftest.subprocess, "run",
                        lambda *a, **k: FakeCompleted(listing))

    assert _app_uid("ZT322L348J", "com.toppansecurity.walletapp") == "10291"
    assert _app_uid("ZT322L348J", "com.toppansecurity.superapp") == "10241"


def test_a_package_that_is_not_installed_has_no_uid(monkeypatch):
    monkeypatch.setattr(root_conftest.subprocess, "run", lambda *a, **k: FakeCompleted(""))
    assert _app_uid("ZT322L348J", PKG) == ""


def test_adb_failing_to_list_packages_gives_no_uid_rather_than_a_wrong_one(monkeypatch):
    def _boom(*a, **k):
        raise subprocess.TimeoutExpired("adb", 15)

    monkeypatch.setattr(root_conftest.subprocess, "run", _boom)
    assert _app_uid("ZT322L348J", PKG) == ""


# --- and it has to say whether it worked ------------------------------------------------------

def _finish(config):
    root_conftest.pytest_sessionfinish(SimpleNamespace(config=config), 0)


def _session(tmp_path, proc=None, log_file=None):
    config = SimpleNamespace(_run_dir=tmp_path)
    if proc is not None:
        config._logcat_proc = proc
    if log_file is not None:
        config._logcat_file = log_file
    return config


class FakeProc:
    """A logcat process. `alive=False` models adb having given up mid-session."""

    def __init__(self, alive=True):
        self.terminated = False
        self._alive = alive

    def poll(self):
        return None if self._alive else 1

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        return 0


def test_an_empty_capture_is_reported_so_it_cannot_fail_silently_again(tmp_path, caplog):
    path = tmp_path / "logcat.log"
    handle = path.open("w")
    _finish(_session(tmp_path, FakeProc(), handle))

    assert any("did not capture" in r.message and "0 bytes" in r.message
               for r in caplog.records), \
        "a 0-byte log went unnoticed for four months; the run has to say so"


def test_a_working_capture_reports_its_size(tmp_path, caplog):
    path = tmp_path / "logcat.log"
    handle = path.open("w")
    handle.write("x" * 4096)
    _finish(_session(tmp_path, FakeProc(), handle))

    assert any("logcat.log: 4 KB captured" in r.message for r in caplog.records)


def test_the_logcat_process_is_stopped_at_session_end(tmp_path):
    proc = FakeProc()
    _finish(_session(tmp_path, proc, (tmp_path / "logcat.log").open("w")))
    assert proc.terminated, "a leaked adb logcat keeps writing into the finished run's file"


def test_the_digest_reports_its_block_count(tmp_path, caplog):
    (tmp_path / "app.log").write_text(
        "--- TEST: a ---\nx\n\n--- TEST: b ---\ny\n")
    _finish(_session(tmp_path))
    assert any("app.log: 2 failure block(s)" in r.message for r in caplog.records)


def test_retried_blocks_are_counted_without_contradicting_the_summary(tmp_path, caplog):
    """Blocks are per attempt; summary.json reports one outcome per test. Say both."""
    (tmp_path / "app.log").write_text(
        "--- TEST: a ---\nx\n\n--- TEST: a ---\ny\n\n--- TEST: b ---\nz\n")
    _finish(_session(tmp_path))

    reported = " ".join(r.message for r in caplog.records)
    assert "3 failure block(s) across 2 test(s)" in reported


def test_a_clean_run_says_there_was_nothing_to_record(tmp_path, caplog):
    """Otherwise "no app.log" is ambiguous between "no failures" and "broken again"."""
    _finish(_session(tmp_path))
    assert any("no failures to record" in r.message for r in caplog.records)


# --- the scoping decision has to be visible in the artifacts -----------------------------------

def test_the_scope_is_reported_at_session_end(tmp_path, monkeypatch, caplog):
    """Whether the log holds one app or the whole device changes how every line reads.

    It used to be logged from `pytest_configure`, where an INFO record is dropped — pytest's
    logging plugin has not lowered the root level yet — so `test.log` never recorded it and a
    reader could not tell a scoped capture from an unscoped one.
    """
    _fixed_device(monkeypatch, "ZT322L348J")
    monkeypatch.setattr(root_conftest, "_app_uid", lambda serial, package: "10351")
    monkeypatch.setattr(root_conftest.subprocess, "run", lambda *a, **k: FakeCompleted())

    monkeypatch.setattr(root_conftest.subprocess, "Popen", lambda *a, **k: FakeProc())
    config = _config("hovi")
    config._run_dir = tmp_path
    start_logcat(config, tmp_path)
    (tmp_path / "logcat.log").write_text("x" * 2048)
    config._logcat_file = (tmp_path / "logcat.log").open("a")

    root_conftest.pytest_sessionfinish(SimpleNamespace(config=config), 0)

    reported = " ".join(r.message for r in caplog.records)
    assert "uid 10351,1000 (app + system)" in reported
    assert "minus Finsky, appium" in reported, "a reader must see what was removed"


def test_the_unscoped_fallback_says_so_rather_than_looking_scoped(monkeypatch):
    """A device-wide log that reads as app-scoped invites wrong conclusions about other apps."""
    monkeypatch.setattr(root_conftest, "_app_uid", lambda serial, package: "")
    scope = root_conftest._logcat_scope(_logcat_command("ZT322L348J", PKG))

    assert "whole device" in scope and "uid was unknown" in scope


def test_the_install_window_keeps_play_store_logs_but_not_appium_chatter(monkeypatch):
    """The unscoped window is the *install* window, so Finsky is the evidence, not the noise.

    Measured on a fresh emulator install: the fallback carried 1,925 `appium` lines duplicating
    appium.log, and 1,263 Finsky lines. Only the first is redundant — if an install fails, the
    Play Store's own logs are the only place that says why.
    """
    monkeypatch.setattr(root_conftest, "_app_uid", lambda serial, package: "")
    argv = _logcat_command("ZT322L348J", PKG)

    assert "appium:S" in argv
    assert not [a for a in argv if a.startswith("Finsky")], \
        "silencing the Play Store during the install window removes the install evidence"
    assert argv[-1] == "*:V"


def test_a_refused_capture_is_not_reported_as_a_successful_one(tmp_path, caplog):
    """`stderr` goes into the file now, so a refusal is small but not empty.

    "adb: error: more than one device/emulator" is ~60 bytes. A size check alone called that a
    success and printed a reassuring "0 KB captured" — the same reassuring silence this whole
    change exists to remove, just one layer further out.
    """
    path = tmp_path / "logcat.log"
    path.write_text("adb: error: more than one device/emulator\n")
    config = _session(tmp_path, FakeProc(alive=False), path.open("a"))
    _finish(config)

    warned = [r.message for r in caplog.records if "did not capture" in r.message]
    assert warned, "a refused capture was reported as a successful one"
    assert "more than one device" in warned[0], "the reader needs adb's own words"


def test_a_capture_whose_adb_died_mid_session_is_reported(tmp_path, caplog):
    """A healthy capture is still running at session end; an exited one gave up early."""
    path = tmp_path / "logcat.log"
    path.write_text("09-11 10:00:00 I/Something( 100): a few lines then nothing\n" * 40)
    _finish(_session(tmp_path, FakeProc(alive=False), path.open("a")))

    assert any("exited before the session ended" in r.message for r in caplog.records)
