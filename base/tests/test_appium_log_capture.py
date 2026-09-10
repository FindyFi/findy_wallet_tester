"""The Appium log survives a fixture-setup failure — the case that most needs it.

`capture_appium_logs` lived only in the per-wallet `teardown_test`, which runs after
`_ensure_home`'s yield. When `navigate_to_home` raises, that yield is never reached, so the whole
teardown is skipped. hovi's 2026-09-09 run was exactly that — every test dying in setup — and hovi
is the one wallet directory in that run with no `appium.log` at all, while every other wallet has
11-21 MB of it. The failure mode that most needs the Appium log was the only one that could not
produce one.

It is now also captured from the `app` fixture's teardown, which does run after a setup error (the
same fallback position that already catches the screenshot and XML dump). `_appium_captured` keeps
a normal test to exactly one block.

Teardown order and node identity — the wallet's `_ensure_home` teardown runs before the `app`
fixture's, and `request.node` is the same object in both, so the flag is visible — were verified
with a real pytest run over the same fixture topology.
"""
from types import SimpleNamespace

import pytest

from base.conftest_helpers import capture_appium_logs, teardown_test


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


class TalkativeDriver:
    def __init__(self):
        self.drained = 0

    def get_log(self, kind):
        # `get_log` drains the buffer: a second call returns only what arrived since the first.
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
    capture_appium_logs(driver, tmp_path, "test_x")


def test_entries_are_written_as_one_labelled_block(tmp_path):
    capture_appium_logs(TalkativeDriver(), tmp_path, "test_credential_issuance_hovi")

    body = (tmp_path / "appium.log").read_text()
    assert body.count("--- TEST: test_credential_issuance_hovi ---") == 1
    assert "hello from appium" in body
    assert "[INFO]" in body


def test_nothing_is_written_when_there_is_nothing_to_write(tmp_path):
    capture_appium_logs(EmptyDriver(), tmp_path, "test_x")
    assert not (tmp_path / "appium.log").exists(), "an empty block is noise, not evidence"


def test_wallet_teardown_marks_the_test_so_the_fallback_does_not_duplicate(tmp_path):
    """`teardown_test` must claim the capture, or every test gets a second near-empty block."""
    node = SimpleNamespace(name="test_x", nodeid="test_x", _artifact_captured=True)
    node.get_closest_marker = lambda name: None
    request = SimpleNamespace(config=SimpleNamespace(_run_dir=tmp_path), node=node)
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
