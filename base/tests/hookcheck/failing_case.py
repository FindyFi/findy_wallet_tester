"""A deliberately failing test, run as a child session by test_error_screen_hook.py.

Deliberately named so pytest's default `python_files` (`test_*.py`) does not collect it: it must
run only when handed to pytest explicitly, or it would fail the real suite on every run. It lives
inside the repo because that is the only way the child session loads the root `conftest.py` — the
hook under test is defined there, and testing a copy of it would prove nothing.
"""
import json
import os
import pytest

from base.outcome import FlowFailure, REJECTED

_OUT = "HOOKCHECK_OUT"


@pytest.fixture
def report_what_the_hook_left(request):
    yield
    # `pytest_runtest_makereport` in the root conftest has run by the time a teardown does — the
    # same ordering the real digest relies on.
    (open(os.environ[_OUT], "w")).write(json.dumps({
        "line": getattr(request.node, "_failure_line", None),
        "category": getattr(request.node, "_failure_category", None),
    }))


def test_a_wallet_rejection(report_what_the_hook_left):
    raise FlowFailure(REJECTED, "[credential_flow] hovi accepted the offer and then failed: "
                                '"Something went wrong"\nthis second line must not be kept')
