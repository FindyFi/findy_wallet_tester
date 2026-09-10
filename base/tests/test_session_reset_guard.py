"""A session reset that has burned its reruns is not attempted again.

`_session_reset_done` is set only after `init_flow.run()` returns, so a reset that always fails used
to be retried by every test in the wallet. On 2026-09-09 that turned one bricked hovi into 11 tests
x 3 attempts x ~2 minutes: 84 minutes of wall clock, 10 identical error cells, zero signal.

The balance these tests pin down: the first test keeps its full rerun budget, because a
wipe-and-onboard is genuinely flaky and one bad attempt should not condemn the wallet — but once
*that* test has given up, every later test fails in milliseconds carrying the original cause.
Failing fast, not passing: a reset that did not happen must never let a test run against a wallet
that still holds credentials (`max_credentials` is 0 everywhere).

`request.node.nodeid` being stable across rerun attempts is what makes "the same test" meaningful.
Verified against pytest-rerunfailures 16.0.1: three attempts of one test report one nodeid, and
`request.config` is a single object for the whole session.
"""
from types import SimpleNamespace

import pytest

from base.conftest_helpers import navigate_to_home


class FlowSpy:
    """Records every reset actually attempted; optionally fails them all."""

    def __init__(self, fail=False):
        self.attempts = []
        self.fail = fail

    def run(self, driver, **kwargs):
        self.attempts.append(kwargs.get("skip_if_done"))
        if self.fail:
            raise RuntimeError("App stuck in unknown state even after restart")

    @property
    def wipes(self):
        return self.attempts.count(False)


def _app(skip_if_done=False):
    return SimpleNamespace(
        driver=object(),
        page_args={},
        config={
            "application": {"pin": "123456", "package": "droidwallet.hovi.id"},
            "onboarding": {"skip_if_done": skip_if_done},
        },
    )


def _request(config, nodeid):
    return SimpleNamespace(config=config, node=SimpleNamespace(nodeid=nodeid))


def _attempt(config, nodeid, flow):
    """Returns 'ok', 'attempted' (a real reset that failed) or 'fast' (refused without trying)."""
    try:
        navigate_to_home(_app(), _request(config, nodeid), flow)
        return "ok"
    except RuntimeError as e:
        return "fast" if "not retrying" in str(e) else "attempted"


def test_a_broken_reset_is_tried_once_per_run_not_once_per_test():
    """The 2026-09-09 shape: 11 tests, 3 attempts each, reset never succeeds."""
    config = SimpleNamespace()
    flow = FlowSpy(fail=True)
    outcomes = [
        _attempt(config, f"test_{i}", flow)
        for i in range(11)
        for _ in range(3)
    ]

    assert flow.wipes == 3, "only the first test's rerun budget should reach the device"
    assert outcomes[:3] == ["attempted"] * 3
    assert set(outcomes[3:]) == {"fast"}
    assert len(outcomes) == 33


def test_the_first_test_keeps_its_reruns_so_a_flaky_reset_can_recover():
    config = SimpleNamespace()
    assert _attempt(config, "test_0", FlowSpy(fail=True)) == "attempted"

    # same test, second attempt — must be allowed to try again, and it succeeds
    good = FlowSpy()
    assert _attempt(config, "test_0", good) == "ok"
    assert good.wipes == 1
    assert config._session_reset_done is True


def test_later_tests_skip_instead_of_wiping_again_once_the_reset_succeeded():
    config = SimpleNamespace()
    _attempt(config, "test_0", FlowSpy())

    flow = FlowSpy()
    assert _attempt(config, "test_1", flow) == "ok"
    assert flow.attempts == [True], "a second wipe would destroy the first one's work"


def test_the_failure_is_reported_verbatim_so_the_cause_survives():
    config = SimpleNamespace()
    _attempt(config, "test_0", FlowSpy(fail=True))

    with pytest.raises(RuntimeError) as exc:
        navigate_to_home(_app(), _request(config, "test_1"), FlowSpy(fail=True))
    assert "App stuck in unknown state" in str(exc.value), "the real cause must not be swallowed"


def test_gataca_calls_navigate_to_home_twice_per_test_and_still_wipes_once():
    """gataca re-navigates after `ensure_did`; the second call must not trigger another wipe."""
    config = SimpleNamespace()
    flow = FlowSpy()
    _attempt(config, "test_0", flow)   # the wipe
    _attempt(config, "test_0", flow)   # after ensure_did
    assert flow.attempts == [False, True]


def test_wallets_without_reset_are_untouched():
    flow = FlowSpy()
    navigate_to_home(_app(skip_if_done=True), _request(SimpleNamespace(), "test_0"), flow)
    assert flow.attempts == [True]
    assert flow.wipes == 0
