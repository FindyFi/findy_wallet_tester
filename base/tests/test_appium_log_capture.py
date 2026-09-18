"""appium.log holds each Appium entry once, under the test that produced it.

Two independent defects met in this file.

**The capture point.** `capture_appium_logs` lived only in the per-wallet `teardown_test`, which
runs after `_ensure_home`'s yield. When `navigate_to_home` raises, that yield is never reached, so
the whole teardown is skipped. hovi's 2026-09-09 run was exactly that — every test dying in setup —
and hovi is the one wallet directory in that run with no `appium.log` at all, while every other
wallet has 11-21 MB of it. The failure mode that most needs the Appium log was the only one that
could not produce one. It is now also captured from the `app` fixture's teardown, which does run
after a setup error (the same fallback position that already catches the screenshot and XML dump);
`_appium_captured` keeps a normal test to exactly one block.

Teardown order and node identity — the wallet's `_ensure_home` teardown runs before the `app`
fixture's, and `request.node` is the same object in both, so the flag is visible — were verified
with a real pytest run over the same fixture topology.

**What went into the block.** `driver.get_log('server')` was assumed to drain the buffer. It does
not: it returns the most recent ~3000 entries on every call. Measured on authbound's 2026-09-10
run, adjacent blocks shared 42-77% of their lines, the file held 108,781 lines carrying 31,009
distinct ones (71% duplication, 22 MB), and its first block spanned 10:06:29 to 11:11:30 for a
session that started at 11:11:18 — an hour of a *previous run*, filed under `test_app_launch`. So
a block was not evidence about the test named in its header. A cursor on the pytest config fixes
both: newer than the last entry written, and not older than this wallet's session.
"""
from types import SimpleNamespace

import pytest

from base.conftest_helpers import capture_appium_logs, teardown_test


def _request(tmp_path, *, started_at=0, node=None):
    return SimpleNamespace(
        config=SimpleNamespace(_run_dir=tmp_path, _session_started_at=started_at),
        node=node or SimpleNamespace(name="test_x", nodeid="test_x"),
    )


class DeadDriver:
    """A session that died with the app — the likely state after a setup failure."""

    def get_log(self, kind):
        raise Exception("A session is either terminated or not started")


class UnsupportedDriver:
    def get_log(self, kind):
        raise Exception("Unsupported log type 'server'")


class EmptyDriver:
    def get_log(self, kind):
        return []


class RollingDriver:
    """The real behaviour: every call returns the last `window` entries, not just the new ones."""

    def __init__(self, window=4):
        self.entries = []
        self.window = window

    def emit(self, ts_ms, message):
        self.entries.append({"timestamp": ts_ms, "level": "info", "message": message})

    def get_log(self, kind):
        return list(self.entries[-self.window:])


class TalkativeDriver:
    def __init__(self):
        self.drained = 0

    def get_log(self, kind):
        self.drained += 1
        if self.drained > 1:
            return []
        return [{"timestamp": 1757491000000, "level": "info", "message": "hello from appium"}]


@pytest.mark.parametrize(
    "driver", [DeadDriver(), UnsupportedDriver(), EmptyDriver(), TalkativeDriver()],
    ids=["dead_session", "unsupported", "empty_buffer", "with_entries"],
)
def test_capture_never_raises_into_teardown(driver, tmp_path):
    """A second exception here would mask the real failure and lose the run's evidence."""
    capture_appium_logs(driver, _request(tmp_path), "test_x")


def test_entries_are_written_as_one_labelled_block(tmp_path):
    capture_appium_logs(TalkativeDriver(), _request(tmp_path),
                        "test_credential_issuance_hovi")

    body = (tmp_path / "appium.log").read_text()
    assert body.count("--- TEST: test_credential_issuance_hovi ---") == 1
    assert "hello from appium" in body
    assert "[INFO]" in body


def test_nothing_is_written_when_there_is_nothing_to_write(tmp_path):
    capture_appium_logs(EmptyDriver(), _request(tmp_path), "test_x")
    assert not (tmp_path / "appium.log").exists(), "an empty block is noise, not evidence"


def test_wallet_teardown_marks_the_test_so_the_fallback_does_not_duplicate(tmp_path):
    """`teardown_test` must claim the capture, or every test gets a second near-empty block."""
    node = SimpleNamespace(name="test_x", nodeid="test_x", _artifact_captured=True)
    node.get_closest_marker = lambda name: None
    request = _request(tmp_path, node=node)
    app = SimpleNamespace(
        driver=TalkativeDriver(),
        page_args={},
        config={"application": {"pin": "p", "package": "pkg"}},
    )

    class Flow:
        @staticmethod
        def run(driver, **kwargs):
            pass

    teardown_test(app, request, Flow)

    assert node._appium_captured is True
    # which is what the `app` fixture's fallback consults
    assert getattr(node, "_appium_captured", False) is True


# --- one entry, one block ---------------------------------------------------------------------

def _blocks(tmp_path):
    """appium.log as {test name: [messages]}."""
    out, name = {}, None
    for line in (tmp_path / "appium.log").read_text().splitlines():
        if line.startswith("--- TEST:"):
            name = line[len("--- TEST: "):-len(" ---")]
            out[name] = []
        elif line.strip() and name:
            out[name].append(line.split("] ", 1)[-1])
    return out


def test_a_rolling_buffer_does_not_repeat_earlier_tests_in_every_later_block(tmp_path):
    """The 71%-duplication defect, in miniature."""
    driver = RollingDriver(window=4)
    request = _request(tmp_path, started_at=1000)

    driver.emit(1001, "a1")
    driver.emit(1002, "a2")
    capture_appium_logs(driver, request, "test_a")

    driver.emit(1003, "b1")
    capture_appium_logs(driver, request, "test_b")

    driver.emit(1004, "c1")
    driver.emit(1005, "c2")
    capture_appium_logs(driver, request, "test_c")

    blocks = _blocks(tmp_path)
    assert blocks == {"test_a": ["a1", "a2"], "test_b": ["b1"], "test_c": ["c1", "c2"]}


def test_the_buffers_backlog_from_a_previous_run_is_not_filed_under_the_first_test(tmp_path):
    """authbound's first block held an hour of the 10:04 run under `test_app_launch`."""
    driver = RollingDriver(window=10)
    driver.emit(500, "left over from the 10:04 run")
    driver.emit(600, "and more of it")

    request = _request(tmp_path, started_at=1000)
    driver.emit(1001, "this session's first entry")
    capture_appium_logs(driver, request, "test_app_launch")

    assert _blocks(tmp_path) == {"test_app_launch": ["this session's first entry"]}


def test_two_entries_in_the_same_millisecond_are_both_kept_but_not_repeated(tmp_path):
    """The cursor is a timestamp, so a same-ms tie needs the message to break it."""
    driver = RollingDriver(window=10)
    request = _request(tmp_path, started_at=1000)

    driver.emit(2000, "same-ms one")
    driver.emit(2000, "same-ms two")
    capture_appium_logs(driver, request, "test_a")

    driver.emit(2000, "same-ms three")
    capture_appium_logs(driver, request, "test_b")

    blocks = _blocks(tmp_path)
    assert blocks["test_a"] == ["same-ms one", "same-ms two"]
    assert blocks["test_b"] == ["same-ms three"], "a same-ms entry was dropped or repeated"


def test_nothing_new_means_no_block_at_all(tmp_path):
    driver = RollingDriver(window=10)
    request = _request(tmp_path, started_at=1000)
    driver.emit(1001, "only entry")
    capture_appium_logs(driver, request, "test_a")
    capture_appium_logs(driver, request, "test_b")

    assert list(_blocks(tmp_path)) == ["test_a"], "test_b re-wrote what test_a already had"


def test_a_server_clock_behind_ours_still_gets_logged_rather_than_silenced(tmp_path):
    """Silence is the failure mode this change exists to end; it must not reintroduce one."""
    driver = RollingDriver(window=10)
    driver.emit(500, "server clock is behind this machine's")
    request = _request(tmp_path, started_at=1_000_000)

    capture_appium_logs(driver, request, "test_a")

    assert _blocks(tmp_path) == {"test_a": ["server clock is behind this machine's"]}


def test_the_cursor_is_per_wallet_not_per_process(tmp_path):
    """`run_tests.py` runs the whole fleet in one process; a shared cursor would leak across it."""
    driver = RollingDriver(window=10)
    driver.emit(1001, "hovi's entry")
    hovi = _request(tmp_path / "hovi", started_at=1000)
    (tmp_path / "hovi").mkdir()
    capture_appium_logs(driver, hovi, "test_a")

    # Next wallet, next pytest.main() — a fresh config object, so a fresh cursor.
    driver.emit(1002, "toppan's entry")
    toppan = _request(tmp_path / "toppan", started_at=1002)
    (tmp_path / "toppan").mkdir()
    capture_appium_logs(driver, toppan, "test_a")

    assert _blocks(tmp_path / "hovi") == {"test_a": ["hovi's entry"]}
    assert _blocks(tmp_path / "toppan") == {"test_a": ["toppan's entry"]}, \
        "the second wallet inherited the first wallet's entries"
