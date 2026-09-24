"""A wallet whose `app` fixture dies before its yield still leaves its evidence behind.

pytest schedules a yield fixture's teardown only once the body reaches `yield`. Every capture the
`app` fixture takes — XML dump, screenshot, appium.log block, recording — used to sit after it, so a
raise anywhere in the body lost all four at once. unime died in the `activate_app` → foreground
wait 11 tests x 3 attempts in run 2026-09-10_11-11-18, and its directory held no artifacts at all.

The fix is a finalizer registered right after the recording starts. Two properties of pytest make
it easy to get subtly wrong, and neither is visible to a unit test, so this runs the real root
conftest in a child session (the same approach as test_error_screen_hook.py):

  * **finalizers run LIFO**, and the yield teardown is registered only once the body yields — so a
    finalizer registered earlier runs *after* the post-yield code on a normal test, which is after
    HOME and the recents clear. Capturing there would dump the home screen. It must be a fallback
    that finds its work already done;
  * **a rerun reuses the item**, so capture flags left True by one attempt would make the next
    attempt's fallback skip its captures.

The case file is `hookcheck/app_fixture_case.py`; each of its tests is one scenario below.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

CASE = Path(__file__).parent / "hookcheck" / "app_fixture_case.py"


@pytest.fixture(scope="module")
def child(tmp_path_factory):
    """Run the case file once; return what each attempt's driver saw, plus the run directory."""
    out = tmp_path_factory.mktemp("appcheck")
    scratch = tmp_path_factory.mktemp("rundir")
    # TMPDIR: the root conftest gives a non-wallet session a `mkdtemp` run directory, which would
    # otherwise land in the system temp dir and outlive this test.
    env = {**os.environ, "APPCHECK_OUT": str(out), "TMPDIR": str(scratch)}
    result = subprocess.run(
        [sys.executable, "-m", "pytest", str(CASE), "-q", "--reruns", "0", "-p", "no:cacheprovider",
         "-rA"],
        cwd=Path(__file__).parents[2], env=env, capture_output=True, text=True, timeout=180,
    )
    attempts = {}
    for f in out.glob("*.json"):
        name, attempt, _ = f.name.rsplit(".", 2)
        attempts.setdefault(name.split("[")[0], {})[int(attempt)] = json.loads(f.read_text())
    assert attempts, f"the child session recorded nothing:\n{result.stdout[-3000:]}\n{result.stderr[-2000:]}"
    run_dir = Path(next(iter(next(iter(attempts.values())).values()))["run_dir"])
    return {"attempts": attempts, "run_dir": run_dir, "stdout": result.stdout}


def _blocks(path: Path, test: str) -> int:
    """How many `--- TEST: <name> ---` blocks a log holds for one test."""
    if not path.exists():
        return 0
    return path.read_text().count(f"--- TEST: {test}_fakewallet ---")


def _has(run_dir: Path, sub: str, test: str, ext: str) -> bool:
    return (run_dir / sub / f"{test}_fakewallet.{ext}").exists()


def test_a_fixture_that_dies_before_its_yield_still_leaves_every_artifact(child):
    """The unime case: `activate_app` raises, so the post-yield teardown never runs."""
    run_dir, test = child["run_dir"], "test_dies_before_yield"
    missing = [what for what, ok in (
        ("XML dump", _has(run_dir, "xml_dumps", test, "xml")),
        ("screenshot", _has(run_dir, "screenshots", test, "png")),
        ("recording", _has(run_dir, "recordings", test, "mp4")),
    ) if not ok]
    assert not missing, f"lost when the fixture died before yielding: {', '.join(missing)}"
    assert _blocks(run_dir / "appium.log", test) == 1, "the Appium log block was lost"
    assert _blocks(run_dir / "app.log", test) == 1, "the failure digest block was lost"


def test_a_normal_failure_is_captured_once_and_before_home(child):
    """The finalizer runs after the post-yield code, so it must find its work already done."""
    run_dir, test = child["run_dir"], "test_fails_in_body"
    calls = child["attempts"][test][1]["calls"]

    assert calls.count("page_source") == 1, f"XML dumped {calls.count('page_source')} times"
    assert calls.count("stop_recording_screen") == 1
    assert _blocks(run_dir / "appium.log", test) == 1, "a normal test must get exactly one block"
    assert calls.index("page_source") < calls.index("press_keycode:3"), (
        "the dump was taken after HOME — it would show the launcher, not the failing screen"
    )


def test_a_wallet_teardown_that_captures_first_is_not_duplicated(child):
    """Every wallet's `_ensure_home` captures before the `app` fixture's teardown and sets the
    flags itself. Neither the post-yield code nor the finalizer behind it may capture again."""
    run_dir, test = child["run_dir"], "test_fails_behind_a_wallet_teardown"
    calls = child["attempts"][test][1]["calls"]

    assert calls.count("page_source") == 1, f"XML dumped {calls.count('page_source')} times"
    assert _blocks(run_dir / "appium.log", test) == 1
    assert _blocks(run_dir / "app.log", test) == 1
    assert calls.index("page_source") < calls.index("press_keycode:3")


def test_a_passing_test_saves_no_artifacts_and_one_appium_block(child):
    run_dir, test = child["run_dir"], "test_passes"
    calls = child["attempts"][test][1]["calls"]

    assert "page_source" not in calls and "save_screenshot" not in calls
    assert not _has(run_dir, "xml_dumps", test, "xml")
    assert calls.count("stop_recording_screen") == 1
    assert _blocks(run_dir / "appium.log", test) == 1


def test_a_rerun_that_dies_early_is_not_skipped_on_the_previous_attempts_flags(child):
    """Attempt 1's teardown sets every capture flag; attempt 2 dies before yielding."""
    run_dir, test = child["run_dir"], "test_rerun_then_dies"
    attempts = child["attempts"][test]
    assert sorted(attempts) == [1, 2], "the rerun did not happen, so this proves nothing"

    second = attempts[2]["calls"]
    assert "page_source" in second and "stop_recording_screen" in second, (
        "attempt 2 captured nothing: it inherited attempt 1's flags"
    )
    assert _blocks(run_dir / "appium.log", test) == 2, "one block per attempt"


def test_a_fallback_whose_captures_raise_does_not_bury_the_original_failure(child):
    """Every capture raises. The test must still report its setup error, and only that."""
    assert "ERROR at setup of test_fallback_with_a_raising_driver" in child["stdout"]
    assert "ERROR at teardown of test_fallback_with_a_raising_driver" not in child["stdout"], (
        "a capture raised out of the finalizer — pytest reports that as a second error"
    )
    calls = child["attempts"]["test_fallback_with_a_raising_driver"][1]["calls"]
    # Each capture is guarded on its own: the first two raising must not cost the recording its
    # attempt.
    assert calls[-3:] == ["save_failure_artifacts", "capture_appium_logs", "stop_recording_screen"]
