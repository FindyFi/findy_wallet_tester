"""Flows that record progress and then fail, run as a child session by test_progress_trail.py.

Not named `test_*.py`, so the real suite never collects it: both tests fail on purpose. It sits in
the repo so the child session loads the real root `conftest.py`, whose hooks reset the trail per
attempt and copy it into summary.json and app.log.
"""
import json
import os

import pytest

from base import progress

_ATTEMPTS = {}


@pytest.fixture
def report_run_dir(request):
    yield
    with open(os.environ["HOOKCHECK_OUT"], "w") as f:
        json.dump({"run_dir": str(request.config._run_dir)}, f)


def test_a_bare_failure_after_two_stages(report_run_dir):
    progress.reached(progress.DELIVER, "deeplink delivered")
    progress.reached(progress.PRESENT, "offer shown")
    # The failure whose position used to be unknown: no category, no context.
    raise Exception("Element ('xpath', '//*[@text=\"Accept\"]') not found")


@pytest.mark.flaky(reruns=1)
def test_a_rerun_that_fails_at_once(request, report_run_dir):
    _ATTEMPTS[request.node.nodeid] = _ATTEMPTS.get(request.node.nodeid, 0) + 1
    if _ATTEMPTS[request.node.nodeid] == 1:
        progress.reached(progress.DELIVER, "deeplink delivered")
        progress.reached(progress.ACCEPT, "offer accepted")
    raise RuntimeError(f"attempt {_ATTEMPTS[request.node.nodeid]} failed")
