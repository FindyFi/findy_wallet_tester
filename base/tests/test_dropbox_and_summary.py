"""Two records that do not depend on us watching, and one that machines can read.

**crashes.log** comes from Android's DropBox, which the system writes and we read afterwards.
Every other artifact in a run directory is captured live, so a crash that takes the session with it
takes its own evidence too; DropBox survives that. It is also structured — process, uid, package
version and build fingerprint alongside the stack — where a logcat line is just a line. The
2026-09-09 hovi investigation is the case in point: the phone's DropBox still holds six
`droidwallet.hovi.id` entries naming `CredentialCard`, and reading them would have replaced a
241 MB logcat archive and a live device session with one `dumpsys`.

ANRs arrive through the same door (`data_app_anr`). `/data/anr/` is listable but **not readable**
without root, so DropBox is the only route to them on an ordinary device.

**summary.json** exists because the only structured record of a run used to be a `data-jsonblob`
attribute inside `report.html`. Scraping it silently produced "?" for every row when the key shape
turned out not to be what a caller assumed — and a missing field looks exactly like a missing test.
"""
import json
import subprocess
from types import SimpleNamespace

import pytest

import conftest as root_conftest
from conftest import capture_dropbox, write_summary

PKG = "droidwallet.hovi.id"

CRASH = """2026-09-10 16:44:01 data_app_crash (text, 10412 bytes)
Process: droidwallet.hovi.id
PID: 30106
Package: droidwallet.hovi.id v34 (1.3.0)
com.facebook.react.common.JavascriptException: TypeError: Cannot read property 'replace' of undefined
    at CredentialCard (address at index.android.bundle:1:1583859)
"""

ANR = """2026-09-10 16:44:30 data_app_anr (text, 8000 bytes)
Process: droidwallet.hovi.id
Subject: Input dispatching timed out
"""

OLD_CRASH = """2026-09-09 11:00:00 data_app_crash (text, 400 bytes)
Process: droidwallet.hovi.id
an older run's crash, before this session began
"""

OTHER_APP = """2026-09-10 16:44:10 data_app_crash (text, 900 bytes)
Process: com.google.android.apps.maps
someone else's crash on a shared device
"""

HEADER = "Drop box contents: 82 entries\nMax entries: 1000\n\n"


def _dumpsys(monkeypatch, stdout, returncode=0):
    monkeypatch.setattr(root_conftest.subprocess, "run",
                        lambda *a, **k: SimpleNamespace(stdout=stdout, stderr="",
                                                        returncode=returncode))


def test_this_sessions_crash_is_captured_with_its_stack(tmp_path, monkeypatch):
    _dumpsys(monkeypatch, HEADER + OLD_CRASH + "\n" + CRASH)
    capture_dropbox(tmp_path, "ZT322L348J", PKG, since="2026-09-10 16:43:00")

    body = (tmp_path / "crashes.log").read_text()
    assert "CredentialCard" in body, "the stack is the entire point"
    assert "Package: droidwallet.hovi.id v34 (1.3.0)" in body, \
        "the build is what makes a crash comparable across runs"
    assert "an older run's crash" not in body


def test_anrs_come_through_the_same_door(tmp_path, monkeypatch):
    """`/data/anr/` is not readable without root, so this is the only route to them."""
    _dumpsys(monkeypatch, HEADER + ANR)
    capture_dropbox(tmp_path, "ZT322L348J", PKG, since="2026-09-10 16:43:00")

    body = (tmp_path / "crashes.log").read_text()
    assert "data_app_anr" in body
    assert "Input dispatching timed out" in body


def test_another_apps_crash_is_not_filed_under_this_wallet(tmp_path, monkeypatch):
    """The devices are shared; a stray crash read as a finding would be worse than no file."""
    _dumpsys(monkeypatch, HEADER + OTHER_APP)
    capture_dropbox(tmp_path, "ZT322L348J", PKG, since="2026-09-10 16:43:00")

    assert not (tmp_path / "crashes.log").exists()


def test_nothing_is_written_when_the_session_was_clean(tmp_path, monkeypatch):
    _dumpsys(monkeypatch, HEADER + OLD_CRASH)
    capture_dropbox(tmp_path, "ZT322L348J", PKG, since="2026-09-10 16:43:00")
    assert not (tmp_path / "crashes.log").exists()


def test_entries_are_kept_whole_not_line_matched(tmp_path, monkeypatch):
    """A stack is only evidence intact — grepping lines would strip the frames off the exception."""
    _dumpsys(monkeypatch, HEADER + CRASH)
    capture_dropbox(tmp_path, "ZT322L348J", PKG, since="2026-09-10 16:43:00")

    body = (tmp_path / "crashes.log").read_text()
    for line in ["PID: 30106", "JavascriptException", "at CredentialCard"]:
        assert line in body


@pytest.mark.parametrize("broken", [
    pytest.param(lambda mp: _dumpsys(mp, "", returncode=1), id="dumpsys_failed"),
    pytest.param(lambda mp: mp.setattr(
        root_conftest.subprocess, "run",
        lambda *a, **k: (_ for _ in ()).throw(subprocess.TimeoutExpired("adb", 120))),
        id="adb_hung"),
], )
def test_a_broken_capture_never_breaks_the_session(tmp_path, monkeypatch, broken):
    broken(monkeypatch)
    capture_dropbox(tmp_path, "ZT322L348J", PKG, since="2026-09-10 16:43:00")  # must not raise


def test_no_device_or_no_package_captures_nothing(tmp_path, monkeypatch):
    called = []
    monkeypatch.setattr(root_conftest.subprocess, "run",
                        lambda *a, **k: called.append(a) or SimpleNamespace(
                            stdout="", stderr="", returncode=0))
    capture_dropbox(tmp_path, "", PKG, since="x")
    capture_dropbox(tmp_path, "ZT322L348J", "", since="x")
    assert called == [], "dumpsys was run without knowing whose crashes to look for"


# --- summary.json ----------------------------------------------------------------------------

def _config(tmp_path, results):
    return SimpleNamespace(
        _run_dir=tmp_path, _results=results, _session_started_at=1757500000000,
        args=["wallets/hovi/tests/test_credential_issuance.py"],
    )


def _record(nodeid, outcome="passed", **kw):
    base = {"nodeid": nodeid, "name": nodeid.split("::")[-1], "params": {},
            "outcome": outcome, "failed_in": "", "error": "", "category": "",
            "duration": 1.0, "attempts": 1}
    base.update(kw)
    return base


def test_the_summary_is_readable_without_parsing_html(tmp_path):
    results = {
        "a::test_app_launch": _record("a::test_app_launch"),
        "b::test_credential_issuance": _record(
            "b::test_credential_issuance", outcome="failed", failed_in="call",
            error="FlowFailure: [rejected] [credential_flow] boom", category="rejected",
            params={"issuer_name": "paradym_issuer"}, attempts=3),
    }
    write_summary(_config(tmp_path, results))

    data = json.loads((tmp_path / "summary.json").read_text())
    assert data["wallet"] == "hovi"
    assert data["totals"] == {"tests": 2, "passed": 1, "failed": 1, "error": 0,
                              "skipped": 0, "reruns": 2}
    failed = [t for t in data["tests"] if t["outcome"] == "failed"][0]
    assert failed["category"] == "rejected", "the outcome vocabulary must survive into the summary"
    assert failed["params"] == {"issuer_name": "paradym_issuer"}, "what it was tested against"
    assert failed["attempts"] == 3, "a test that passed on its third try is not a clean pass"


def test_a_setup_failure_is_an_error_not_a_failure(tmp_path):
    """pytest's distinction, kept: an error means the wallet was never actually tested."""
    results = {"a::t": _record("a::t", outcome="error", failed_in="setup")}
    write_summary(_config(tmp_path, results))

    data = json.loads((tmp_path / "summary.json").read_text())
    assert data["totals"]["error"] == 1 and data["totals"]["failed"] == 0


def test_no_results_writes_no_file(tmp_path):
    write_summary(_config(tmp_path, {}))
    assert not (tmp_path / "summary.json").exists()


def test_a_summary_is_never_worth_failing_a_run_over(tmp_path):
    class Hostile(dict):
        def values(self):
            raise RuntimeError("something is very wrong")

    write_summary(_config(tmp_path, Hostile({"x": 1})))  # must not raise


# --- the session floor -------------------------------------------------------------------------

def test_the_device_clock_is_read_whole(monkeypatch):
    """`adb shell` re-splits its arguments, so the format string must survive as one word.

    Passing it as a separate argv entry delivered `date +%Y-%m-%d` and returned a bare date. The
    floor silently became a whole day, which would file a week of unrelated crashes under one run.
    """
    seen = {}

    def _run(argv, **kw):
        seen["argv"] = argv
        # Emulate the device: everything after `shell` is one command line.
        cmd = argv[argv.index("shell") + 1:]
        assert len(cmd) == 1, f"the format string was split into {cmd}"
        return SimpleNamespace(stdout="2026-09-11 09:29:36\n", stderr="", returncode=0)

    monkeypatch.setattr(root_conftest.subprocess, "run", _run)
    assert root_conftest._device_now("ZT322L348J") == "2026-09-11 09:29:36"


def test_a_date_only_clock_is_rejected_rather_than_used(monkeypatch):
    """The exact bug: a bare date compares as a whole-day floor and over-collects."""
    monkeypatch.setattr(root_conftest.subprocess, "run",
                        lambda *a, **k: SimpleNamespace(stdout="2026-09-11\n", stderr="",
                                                        returncode=0))
    assert root_conftest._device_now("ZT322L348J") == ""


def test_without_a_floor_nothing_is_captured(tmp_path, monkeypatch):
    """No file beats a file claiming this session produced 80 crashes it did not."""
    called = []
    monkeypatch.setattr(root_conftest.subprocess, "run",
                        lambda *a, **k: called.append(a) or SimpleNamespace(
                            stdout=HEADER + CRASH, stderr="", returncode=0))
    capture_dropbox(tmp_path, "ZT322L348J", PKG, since="")

    assert called == [], "dumpsys ran without a time window"
    assert not (tmp_path / "crashes.log").exists()


# --- _record_result: the function the summary is built from ------------------------------------
#
# `write_summary` was tested with hand-built records, so it could not catch a record being built
# wrongly. Every case below drives the real accumulator with real report shapes.

def _rep(when, *, failed=False, skipped=False, duration=1.0):
    return SimpleNamespace(when=when, failed=failed, skipped=skipped, duration=duration)


def _item(nodeid="w/tests/t.py::test_x[hovi]", params=None):
    item = SimpleNamespace(
        nodeid=nodeid, name=nodeid.split("::")[-1],
        config=SimpleNamespace(), callspec=SimpleNamespace(params=params or {"driver": "hovi"}),
    )
    return item


def _record_of(item):
    return item.config._results[item.nodeid]


def test_a_skipped_test_is_not_recorded_as_a_pass():
    """Both skip routes fire in **setup**, so a call-phase condition never sees them.

    `pytest_runtest_setup` skips a case whose provider is unreachable, and `base/test_cases.py`
    marks params with `pytest.mark.skip`. With the phase condition in place every such skip kept
    the "passed" default — unreachable providers were published as passes and `totals.skipped`
    could never be anything but 0.
    """
    item = _item()
    root_conftest._record_result(item, _rep("setup", skipped=True))

    assert _record_of(item)["outcome"] == "skipped"


def test_an_xfail_is_not_recorded_as_a_pass_either():
    item = _item()
    root_conftest._record_result(item, _rep("setup"))
    root_conftest._record_result(item, _rep("call", skipped=True))

    assert _record_of(item)["outcome"] == "skipped"


def test_a_clean_test_is_a_pass():
    item = _item()
    for phase in ("setup", "call", "teardown"):
        root_conftest._record_result(item, _rep(phase))

    record = _record_of(item)
    assert record["outcome"] == "passed" and record["attempts"] == 1


def test_a_setup_failure_is_an_error_and_a_body_failure_is_a_failure():
    for phase, expected in [("setup", "error"), ("call", "failed")]:
        item = _item()
        item._failure_line = "RuntimeError: boom"
        item._failure_category = "rejected"
        if phase == "call":
            root_conftest._record_result(item, _rep("setup"))
        root_conftest._record_result(item, _rep(phase, failed=True))

        record = _record_of(item)
        assert record["outcome"] == expected, f"{phase} -> {record['outcome']}"
        assert record["error"] == "RuntimeError: boom"
        assert record["category"] == "rejected"


def test_a_retry_that_passes_does_not_keep_the_earlier_failures_verdict():
    """The record must describe the final attempt, or a flaky pass reads as a failure."""
    item = _item()
    item._failure_line = "RuntimeError: first attempt"
    root_conftest._record_result(item, _rep("setup"))
    root_conftest._record_result(item, _rep("call", failed=True))
    # second attempt, clean
    del item._failure_line
    root_conftest._record_result(item, _rep("setup"))
    root_conftest._record_result(item, _rep("call"))

    record = _record_of(item)
    assert record["outcome"] == "passed"
    assert record["error"] == "" and record["failed_in"] == ""
    assert record["attempts"] == 2, "how many tries it took is not the same as passing"


def test_the_provider_is_recorded_but_the_wallet_is_not_duplicated():
    item = _item(params={"driver": "hovi", "issuer_name": "waltid_issuer"})
    root_conftest._record_result(item, _rep("setup"))

    assert _record_of(item)["params"] == {"issuer_name": "waltid_issuer"}


def test_recording_never_breaks_a_run():
    broken = SimpleNamespace()  # no nodeid, no config
    root_conftest._record_result(broken, _rep("setup"))  # must not raise


# A real tombstone: no `Process:` and no `Package:` line at all. The process is named by
# `Cmdline:` and by the `>>> pkg <<<` marker, which is why the owner filter has to know both —
# anchoring on Process/Package alone recognised these entries and then discarded them.
UPPER = """2026-09-10 16:44:20 SYSTEM_TOMBSTONE (text, 5000 bytes)
Build fingerprint: 'motorola/fogorow/fogorow:14/UTAS34.82-126-5:user/release-keys'
Cmdline: droidwallet.hovi.id
pid: 7094, tid: 7094, name: roidwallet.hovi  >>> droidwallet.hovi.id <<<
signal 11 (SIGSEGV), code 1 (SEGV_MAPERR), fault addr 0x0
    #00 pc 00000000000abcde  /apex/com.android.runtime/lib64/bionic/libc.so
"""

LOOKALIKE = """2026-09-10 16:44:25 data_app_crash (text, 500 bytes)
Process: droidwallet.hovi.id.debug
a different package that merely starts with ours
"""


def test_uppercase_tags_are_recognised_as_entries(tmp_path, monkeypatch):
    """emulator-5560 carries SYSTEM_BOOT and SYSTEM_FSCK today; native crashes are
    SYSTEM_TOMBSTONE. An unmatched header is worse than ignored — its body is appended to
    whichever entry is being accumulated, so it can smuggle other text into a kept block."""
    _dumpsys(monkeypatch, HEADER + UPPER)
    capture_dropbox(tmp_path, "ZT322L348J", PKG, since="2026-09-10 16:43:00")

    body = (tmp_path / "crashes.log").read_text()
    assert "SYSTEM_TOMBSTONE" in body
    assert "SIGSEGV" in body, "the signal and backtrace are the whole value of a tombstone"
    assert "Process:" not in body, "the fixture must be a real tombstone, not a crash in disguise"


def test_an_unmatched_header_cannot_leak_into_a_kept_entry(tmp_path, monkeypatch):
    """The failure mode behind the regex fix, stated as a property."""
    _dumpsys(monkeypatch, HEADER + CRASH + "\n2026-09-10 16:44:40 SYSTEM_BOOT (text, 10 bytes)\n"
             "unrelated boot text\n")
    capture_dropbox(tmp_path, "ZT322L348J", PKG, since="2026-09-10 16:43:00")

    body = (tmp_path / "crashes.log").read_text()
    assert "unrelated boot text" not in body, \
        "a SYSTEM_BOOT body was absorbed into the wallet's crash entry"


def test_a_package_that_merely_shares_a_prefix_is_not_ours(tmp_path, monkeypatch):
    """`_app_uid` avoids exactly this trap for the uid; the entry filter must not reopen it."""
    _dumpsys(monkeypatch, HEADER + LOOKALIKE)
    capture_dropbox(tmp_path, "ZT322L348J", PKG, since="2026-09-10 16:43:00")

    assert not (tmp_path / "crashes.log").exists()
