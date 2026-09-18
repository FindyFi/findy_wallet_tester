"""The digest's one coupling to pytest: the root conftest keeps the raised exception for it.

`record_error_screen` is unit-tested against fabricated `_failure_line` / `_failure_category`
attributes, which proves the formatting and none of the plumbing. This runs a real child pytest
session against the real root `conftest.py` and checks what its `pytest_runtest_makereport`
wrapper actually leaves on the node, because three things there are assumptions about pytest that
a unit test cannot see:

  * the hook signature takes `call`, so `call.excinfo` reaches the exception object at all — the
    report's `longrepr` is formatted prose, and the outcome category is deliberately an attribute
    on `FlowFailure` rather than something to parse back out of it;
  * a teardown runs after the report exists, which is the ordering the digest depends on;
  * the message is collapsed to one line, so a multi-line exception cannot smear a block across
    the file and make every later block unreadable.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

CASE = Path(__file__).parent / "hookcheck" / "failing_case.py"


def test_the_root_conftest_keeps_the_exception_the_digest_needs(tmp_path):
    out = tmp_path / "hook.json"
    env = {**os.environ, "HOOKCHECK_OUT": str(out)}
    result = subprocess.run(
        [sys.executable, "-m", "pytest", str(CASE), "-q", "--reruns", "0", "-p", "no:cacheprovider"],
        cwd=Path(__file__).parents[2], env=env, capture_output=True, text=True, timeout=180,
    )

    assert out.exists(), (
        "the child session never reached its teardown, so nothing can be concluded about the "
        f"hook:\n{result.stdout[-3000:]}\n{result.stderr[-2000:]}"
    )
    left = json.loads(out.read_text())

    assert left["category"] == "rejected", (
        "the outcome category did not survive to teardown. It is an attribute on FlowFailure so "
        "the report can group by it without matching on prose — reading it needs `call.excinfo`, "
        "which needs `call` in the hook signature"
    )
    assert left["line"] == (
        'FlowFailure: [rejected] [credential_flow] hovi accepted the offer and then failed: '
        '"Something went wrong"'
    )
    assert "this second line must not be kept" not in left["line"], \
        "a multi-line exception would break the one-field-per-line shape of every later block"
