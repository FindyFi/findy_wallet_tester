"""scripts/housekeep_reports.py: what it touches, what it never touches, and verify-before-delete.

Every test builds a fake report tree under tmp_path; nothing reads the real reports/.
"""
import importlib.util
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "housekeep_reports", Path(__file__).resolve().parents[2] / "scripts" / "housekeep_reports.py")
hk = importlib.util.module_from_spec(_SPEC)
sys.modules["housekeep_reports"] = hk  # dataclasses looks the module up by name
_SPEC.loader.exec_module(hk)

NOW = datetime(2026, 11, 13, 12, 0, 0)
ORIGINAL_BUSY = hk.busy_reason
needs_zstd = pytest.mark.skipif(not shutil.which("zstd"), reason="zstd missing")
needs_ffmpeg = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
                                  reason="ffmpeg missing")
MAC_PYTHON = ("/Library/Developer/CommandLineTools/Library/Frameworks/Python3.framework/Versions/"
              "3.9/Resources/Python.app/Contents/MacOS/Python")
LOG_TEXT = ("get_log line that repeats\n" * 4000).encode()


@pytest.fixture(autouse=True)
def _quiet_machine(monkeypatch, tmp_path):
    """A real nightly lock or a pytest process (this one) must not block the tests."""
    monkeypatch.setattr(hk, "busy_reason", lambda: None)
    monkeypatch.setattr(hk, "APPLY_LOCK", tmp_path / "state" / "apply.lock")


def make_run(root, name, wallets=("hovi",), finished=True, logs=True):
    run = root / name
    for w in wallets:
        wd = run / w
        (wd / "recordings").mkdir(parents=True)
        if finished:
            (wd / "report.html").write_text("<html></html>")
        (wd / "summary.json").write_text("{}")
        (wd / "app.log").write_text("app")
        if logs:
            (wd / "appium.log").write_bytes(LOG_TEXT)
            (wd / "logcat.log").write_bytes(LOG_TEXT)
    return run


def make_mp4(path, height=1200, seconds=1.5):
    width = height * 9 // 20 // 2 * 2
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i",
                    f"testsrc=size={width}x{height}:rate=10:duration={seconds}",
                    "-pix_fmt", "yuv420p", str(path)], check=True)


def snapshot(root):
    return {str(p.relative_to(root)): (p.stat().st_size, p.stat().st_mtime_ns)
            for p in sorted(root.rglob("*"))}


def run_main(root, tmp_path, *extra):
    """Runs main with protection off unless the test says otherwise."""
    return hk.main([str(root), "--log", str(tmp_path / "actions.jsonl"), "--keep-newest", "0",
                    *extra], now=NOW)


def tree(tmp_path, names):
    root = tmp_path / "reports"
    root.mkdir()
    for n in names:
        make_run(root, n)
    return root


OLD = "2026-11-01_10-00-00"      # 12 days old
MID = "2026-11-10_10-00-00"      # 3 days old
FRESH = "2026-11-13_09-00-00"    # today


# --- age and protection ---------------------------------------------------------------------

def test_age_comes_from_the_folder_name_not_mtime(tmp_path):
    root = tree(tmp_path, [OLD])
    os.utime(root / OLD, (NOW.timestamp(), NOW.timestamp()))  # mtime says "now"
    assert hk.run_age_days(OLD, NOW) == pytest.approx(12.08, abs=0.01)
    tasks, _ = hk.plan_tree(root, NOW, 0, 2, 7, {"logs"})
    assert {t.path.name for t in tasks} == {"appium.log", "logcat.log"}


def test_non_timestamp_folders_are_not_runs(tmp_path):
    root = tree(tmp_path, [OLD])
    make_run(root, "scratch")
    assert hk.run_age_days("scratch", NOW) is None
    assert [r.name for r in hk.discover_runs(root)] == [OLD]


def test_newest_runs_are_never_touched(tmp_path):
    names = [OLD, "2026-11-02_10-00-00", "2026-11-03_10-00-00", "2026-11-04_10-00-00"]
    root = tree(tmp_path, names)
    tasks, notes = hk.plan_tree(root, NOW, 2, 2, 7, {"logs"})
    touched = {t.run.name for t in tasks}
    assert touched == set(names[:2])
    assert {n for n, why in notes if "protected" in why} == set(names[2:])


@needs_zstd
def test_default_protection_leaves_the_newest_four_untouched_when_applying(tmp_path):
    root = tree(tmp_path, [OLD, "2026-11-02_10-00-00", "2026-11-03_10-00-00"])
    before = snapshot(root)
    assert hk.main([str(root), "--apply", "--log", str(tmp_path / "a.jsonl")], now=NOW) == 0
    assert snapshot(root) == before


def test_logs_wait_two_days_and_video_waits_seven(tmp_path, monkeypatch):
    root = tree(tmp_path, [OLD, MID, FRESH])
    make_mp4_stub = root / MID / "hovi" / "recordings" / "a.mp4"
    make_mp4_stub.write_bytes(b"x")
    (root / OLD / "hovi" / "recordings" / "b.mp4").write_bytes(b"x")
    monkeypatch.setattr(hk, "probe_video", lambda p: (1612, 10.0))
    tasks, _ = hk.plan_tree(root, NOW, 0, 2, 7, {"logs", "video"})
    got = {(t.run.name, t.phase) for t in tasks}
    assert (MID, "logs") in got and (OLD, "logs") in got and (FRESH, "logs") not in got
    assert (OLD, "video") in got and (MID, "video") not in got


def test_unfinished_wallet_without_report_html_is_skipped(tmp_path):
    root = tmp_path / "reports"
    root.mkdir()
    make_run(root, OLD, wallets=("hovi",), finished=False)
    make_run(root, OLD, wallets=("gataca",))
    tasks, notes = hk.plan_tree(root, NOW, 0, 2, 7, {"logs"})
    assert {t.path.parent.name for t in tasks} == {"gataca"}
    assert any("hovi" in n and "report.html" in why for n, why in notes)


def test_summary_json_is_not_required(tmp_path):
    root = tree(tmp_path, [OLD])
    (root / OLD / "hovi" / "summary.json").unlink()  # older code never wrote it
    tasks, _ = hk.plan_tree(root, NOW, 0, 2, 7, {"logs"})
    assert len(tasks) == 2


# --- refusal --------------------------------------------------------------------------------

def test_busy_when_the_nightly_lock_exists(tmp_path):
    lock = tmp_path / "nightly.lock"
    lock.write_text("")
    assert "nightly" in ORIGINAL_BUSY(lock=lock, ps_output="")
    lock.unlink()
    assert ORIGINAL_BUSY(lock=lock, ps_output="") is None


@pytest.mark.parametrize("command", [
    "/usr/bin/python3 runners/run_tests.py --wallet hovi",
    ".venv/bin/python3 -m pytest wallets/hovi/tests",
    f"{MAC_PYTHON} runners/run_tests.py procivis",
    f"{MAC_PYTHON} runners/run_parallel.py --wait 0 hovi",
    f"{MAC_PYTHON} -m pytest base/tests/",
    f"{MAC_PYTHON} /x/.venv/bin/pytest base/tests -q",
    "/x/.venv/bin/pytest base/tests -q",
])
def test_busy_when_a_test_process_runs(tmp_path, command):
    ps = f"  1 /sbin/launchd\n 4242 {command}\n"
    assert "test process" in ORIGINAL_BUSY(lock=tmp_path / "none", ps_output=ps)


def test_busy_ignores_unrelated_processes(tmp_path):
    ps = ("1 /sbin/launchd\n77 python3 scripts/housekeep_reports.py\n"
          "88 /bin/zsh -c cd x && .venv/bin/python3 -m pytest base/tests\n"
          f"99 {MAC_PYTHON} -m pylance_server\n")
    assert ORIGINAL_BUSY(lock=tmp_path / "none", ps_output=ps) is None


@needs_zstd
def test_apply_refuses_while_busy_but_dry_run_continues(tmp_path, monkeypatch, capsys):
    root = tree(tmp_path, [OLD])
    monkeypatch.setattr(hk, "busy_reason", lambda: "a nightly run is active")
    before = snapshot(root)
    assert run_main(root, tmp_path, "--apply") == 3
    assert snapshot(root) == before
    assert run_main(root, tmp_path) == 0
    assert "warning" in capsys.readouterr().err


# --- dry-run --------------------------------------------------------------------------------

@needs_zstd
def test_dry_run_changes_nothing_and_writes_no_log(tmp_path, capsys):
    root = tree(tmp_path, [OLD, MID])
    before = snapshot(root)
    assert run_main(root, tmp_path) == 0
    assert snapshot(root) == before
    assert not (tmp_path / "actions.jsonl").exists()
    assert "dry-run" in capsys.readouterr().out


# --- apply: logs ----------------------------------------------------------------------------

@needs_zstd
def test_apply_compresses_logs_losslessly_and_keeps_the_rest(tmp_path):
    root = tree(tmp_path, [OLD])
    wd = root / OLD / "hovi"
    assert run_main(root, tmp_path, "--apply") == 0
    for name in ("appium.log", "logcat.log"):
        assert not (wd / name).exists()
        out = subprocess.run(["zstd", "-dc", str(wd / (name + ".zst"))], capture_output=True)
        assert out.stdout == LOG_TEXT
    assert {p.name for p in wd.iterdir() if p.is_file()} >= {"app.log", "report.html",
                                                              "summary.json"}
    assert not list(wd.glob("*.tmp"))
    lines = (tmp_path / "actions.jsonl").read_text().splitlines()
    assert len(lines) == 2 and '"outcome": "done"' in lines[0]


@needs_zstd
def test_failed_verification_keeps_the_original_and_exits_nonzero(tmp_path, monkeypatch):
    root = tree(tmp_path, [OLD])
    wd = root / OLD / "hovi"
    monkeypatch.setattr(hk, "verify_zstd", lambda src, tmp: False)
    assert run_main(root, tmp_path, "--apply") == 1
    assert (wd / "appium.log").read_bytes() == LOG_TEXT
    assert not list(wd.glob("*.zst")) and not list(wd.glob("*.tmp"))
    assert '"outcome": "failed"' in (tmp_path / "actions.jsonl").read_text()


@needs_zstd
def test_a_corrupt_temp_file_fails_the_real_verifier(tmp_path):
    src = tmp_path / "a.log"
    src.write_bytes(LOG_TEXT)
    tmp = tmp_path / "a.log.zst.tmp"
    tmp.write_bytes(b"not zstd at all")
    assert hk.verify_zstd(src, tmp) is False
    subprocess.run(["zstd", "-q", "-f", str(src), "-o", str(tmp)], check=True)
    assert hk.verify_zstd(src, tmp) is True
    src.write_bytes(LOG_TEXT + b"x")  # decompressed stream differs from the original
    assert hk.verify_zstd(src, tmp) is False


@needs_zstd
def test_existing_zst_is_never_overwritten(tmp_path):
    root = tree(tmp_path, [OLD])
    zst = root / OLD / "hovi" / "appium.log.zst"
    zst.write_bytes(b"precious")
    assert run_main(root, tmp_path, "--apply") == 0
    assert zst.read_bytes() == b"precious"
    assert (root / OLD / "hovi" / "appium.log").exists()


@needs_zstd
def test_limit_caps_files_per_phase(tmp_path):
    root = tree(tmp_path, [OLD, "2026-11-02_10-00-00"])
    assert run_main(root, tmp_path, "--apply", "--limit", "1") == 0
    assert len(list(root.rglob("*.zst"))) == 1


@needs_zstd
def test_stale_temp_files_are_deleted_on_apply_only(tmp_path):
    root = tree(tmp_path, [OLD])
    stale = root / OLD / "hovi" / "appium.log.zst.tmp"
    stale.write_text("half")
    stale_mp4 = root / OLD / "hovi" / "recordings" / "x.tmp.mp4"
    stale_mp4.write_text("half")
    run_main(root, tmp_path)
    assert stale.exists() and stale_mp4.exists()
    run_main(root, tmp_path, "--apply")
    assert not stale.exists() and not stale_mp4.exists()
    assert (root / OLD / "hovi" / "appium.log.zst").exists()  # a stale temp is not a skip reason


# --- run folder mtime -----------------------------------------------------------------------

@needs_zstd
def test_run_folder_mtime_is_unchanged_by_apply(tmp_path):
    root = tree(tmp_path, [OLD, MID])
    stamp = datetime(2026, 11, 1, 10, 0, 0).timestamp()
    for run in hk.discover_runs(root):
        os.utime(run, (stamp, stamp))
    before = {r.name: r.stat().st_mtime_ns for r in hk.discover_runs(root)}
    assert run_main(root, tmp_path, "--apply") == 0
    assert {r.name: r.stat().st_mtime_ns for r in hk.discover_runs(root)} == before


@needs_zstd
def test_run_folder_mtime_is_restored_if_something_bumps_it(tmp_path, monkeypatch):
    root = tree(tmp_path, [OLD])
    run = root / OLD
    stamp = datetime(2026, 11, 1, 10, 0, 0).timestamp()
    os.utime(run, (stamp, stamp))
    before = run.stat().st_mtime_ns
    real = hk.process

    def bumping(task):
        (run / "stray").write_text("x")  # changes the run folder itself
        return real(task)

    monkeypatch.setattr(hk, "process", bumping)
    assert run_main(root, tmp_path, "--apply") == 0
    assert run.stat().st_mtime_ns == before


@needs_zstd
def test_nothing_is_created_directly_under_the_root_or_run_folders(tmp_path):
    root = tree(tmp_path, [OLD])
    run_main(root, tmp_path, "--apply")
    assert [p.name for p in root.iterdir()] == [OLD]
    assert [p.name for p in (root / OLD).iterdir()] == ["hovi"]


# --- extra trees ----------------------------------------------------------------------------

@needs_zstd
def test_extra_tree_compresses_big_logcats_only(tmp_path):
    extra = tmp_path / "trouble"
    (extra / "sub").mkdir(parents=True)
    big = extra / "sub" / "logcat_live.log"
    big.write_bytes(LOG_TEXT * 12)  # over 1 MiB
    small = extra / "logcat_small.log"
    small.write_bytes(b"tiny")
    notes = extra / "README.md"
    notes.write_text("keep")
    root = tmp_path / "reports"
    root.mkdir()
    code = hk.main([str(root), "--extra", str(extra), "--apply",
                    "--log", str(tmp_path / "a.jsonl")], now=NOW)
    assert code == 0
    assert not big.exists() and big.with_name("logcat_live.log.zst").exists()
    assert small.exists() and notes.exists()


# --- video ----------------------------------------------------------------------------------

@needs_ffmpeg
def test_video_is_reencoded_with_every_frame_and_idempotent(tmp_path):
    root = tree(tmp_path, [OLD])
    mp4 = root / OLD / "hovi" / "recordings" / "t.mp4"
    make_mp4(mp4)
    frames = hk.count_frames(mp4)
    assert hk.probe_video(mp4)[0] == 1200
    assert run_main(root, tmp_path, "--apply", "--only", "video") == 0
    assert hk.probe_video(mp4)[0] == hk.TARGET_HEIGHT
    assert hk.count_frames(mp4) == frames
    assert not list(mp4.parent.glob("*.tmp.mp4"))
    tasks, _ = hk.plan_tree(root, NOW, 0, 2, 7, {"video"})
    assert tasks == []  # already re-encoded: not touched twice


@needs_ffmpeg
def test_video_failed_verification_keeps_the_original(tmp_path, monkeypatch):
    root = tree(tmp_path, [OLD])
    mp4 = root / OLD / "hovi" / "recordings" / "t.mp4"
    make_mp4(mp4)
    original = mp4.read_bytes()
    monkeypatch.setattr(hk, "verify_video", lambda src, tmp: False)
    assert run_main(root, tmp_path, "--apply", "--only", "video") == 1
    assert mp4.read_bytes() == original
    assert not list(mp4.parent.glob("*.tmp.mp4"))


def _reencode(src, out, vf):
    """A candidate at 806 px built with an arbitrary filter, so only one check can reject it."""
    args = [a for a in hk.ENCODE]
    args[args.index("-vf") + 1] = vf
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(src), *args, str(out)],
                   check=True)


@needs_ffmpeg
def test_verify_video_checks_height_frames_and_duration_separately(tmp_path):
    src = tmp_path / "a.mp4"
    make_mp4(src, seconds=1.5)
    good = tmp_path / "good.mp4"
    _reencode(src, good, "scale=-2:806")
    assert hk.verify_video(src, good) is True
    fewer = tmp_path / "fewer.mp4"  # same duration, about half the frames
    _reencode(src, fewer, "scale=-2:806,fps=5")
    assert hk.probe_video(fewer)[0] == 806 and hk.count_frames(fewer) < hk.count_frames(src)
    assert hk.verify_video(src, fewer) is False
    slow = tmp_path / "slow.mp4"  # same frames, three times as long
    _reencode(src, slow, "scale=-2:806,setpts=3*PTS")
    assert hk.count_frames(slow) == hk.count_frames(src)
    assert hk.verify_video(src, slow) is False
    tall = tmp_path / "tall.mp4"  # right frames and duration, wrong height
    _reencode(src, tall, "scale=-2:1000")
    assert hk.verify_video(src, tall) is False


@needs_ffmpeg
def test_small_recordings_are_left_alone(tmp_path):
    root = tree(tmp_path, [OLD])
    mp4 = root / OLD / "hovi" / "recordings" / "t.mp4"
    make_mp4(mp4, height=600)
    before = mp4.read_bytes()
    assert run_main(root, tmp_path, "--apply", "--only", "video") == 0
    assert mp4.read_bytes() == before


@needs_ffmpeg
def test_unreadable_recordings_are_reported_not_hidden(tmp_path, capsys):
    root = tree(tmp_path, [OLD])
    rec = root / OLD / "hovi" / "recordings"
    (rec / "empty.mp4").write_bytes(b"")
    run_main(root, tmp_path, "--only", "video")
    assert "empty.mp4: ffprobe cannot read it" in capsys.readouterr().out


@needs_zstd
def test_notes_reach_the_action_log_under_apply(tmp_path):
    root = tree(tmp_path, [OLD])
    (root / OLD / "hovi" / "appium.log.zst").write_bytes(b"corrupt")
    run_main(root, tmp_path, "--apply")
    log = (tmp_path / "actions.jsonl").read_text()
    assert '"phase": "note"' in log and "does not match" in log


@needs_zstd
def test_symlinked_logs_are_never_compressed(tmp_path):
    root = tree(tmp_path, [OLD])
    wd = root / OLD / "hovi"
    target = tmp_path / "elsewhere.log"
    target.write_bytes(LOG_TEXT)
    (wd / "appium.log").unlink()
    (wd / "appium.log").symlink_to(target)
    tasks, _ = hk.plan_tree(root, NOW, 0, 2, 7, {"logs"})
    assert [t.path.name for t in tasks] == ["logcat.log"]


@needs_zstd
def test_crash_leftover_original_next_to_a_good_zst_is_finished_off(tmp_path):
    root = tree(tmp_path, [OLD])
    wd = root / OLD / "hovi"
    subprocess.run(["zstd", "-q", str(wd / "appium.log"), "-o", str(wd / "appium.log.zst")],
                   check=True)  # as if the run died before unlinking the original
    assert run_main(root, tmp_path, "--apply") == 0
    assert not (wd / "appium.log").exists()
    out = subprocess.run(["zstd", "-dc", str(wd / "appium.log.zst")], capture_output=True)
    assert out.stdout == LOG_TEXT


@needs_zstd
def test_apply_stops_between_files_when_a_test_run_appears(tmp_path, monkeypatch, capsys):
    root = tree(tmp_path, [OLD])
    calls = iter([None, "a test process is running"])
    monkeypatch.setattr(hk, "busy_reason", lambda: next(calls, "a test process is running"))
    # main's own start check consumes the first None; the second call is before file one
    assert run_main(root, tmp_path, "--apply") == 3
    assert len(list(root.rglob("*.zst"))) == 0
    assert "stopped before" in capsys.readouterr().err


@needs_zstd
def test_apply_stops_after_the_first_file_if_busy_appears_later(tmp_path, monkeypatch):
    root = tree(tmp_path, [OLD])
    calls = iter([None, None])
    monkeypatch.setattr(hk, "busy_reason", lambda: next(calls, "busy now"))
    assert run_main(root, tmp_path, "--apply") == 3
    assert len(list(root.rglob("*.zst"))) == 1


@needs_zstd
def test_second_apply_is_refused_while_the_first_holds_the_lock(tmp_path):
    import fcntl
    root = tree(tmp_path, [OLD])
    hk.APPLY_LOCK.parent.mkdir(parents=True)
    with open(hk.APPLY_LOCK, "w") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        assert run_main(root, tmp_path, "--apply") == 3
    assert not list(root.rglob("*.zst"))


@needs_zstd
def test_limit_is_global_across_roots(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    for r in (a, b):
        r.mkdir()
        make_run(r, OLD)
    code = hk.main([str(a), str(b), "--apply", "--keep-newest", "0", "--limit", "1",
                    "--log", str(tmp_path / "x.jsonl")], now=NOW)
    assert code == 0
    assert len(list(tmp_path.rglob("*.zst"))) == 1


@needs_zstd
def test_an_oserror_on_one_file_fails_it_and_the_rest_continue(tmp_path, monkeypatch):
    root = tree(tmp_path, [OLD])
    real = hk.process
    seen = []

    def flaky(task):
        seen.append(task.path.name)
        if len(seen) == 1:
            raise OSError("disk hiccup")
        return real(task)

    monkeypatch.setattr(hk, "process", flaky)
    assert run_main(root, tmp_path, "--apply") == 1
    assert len(seen) == 2 and len(list(root.rglob("*.zst"))) == 1
    assert (root / OLD / "hovi" / "appium.log").exists()  # the failed file's original is intact


def test_negative_keep_newest_is_rejected(tmp_path):
    root = tree(tmp_path, [OLD])
    with pytest.raises(SystemExit) as exc:
        hk.main([str(root), "--keep-newest", "-1"], now=NOW)
    assert exc.value.code == 2


@needs_zstd
def test_extra_pointing_at_a_reports_tree_is_refused(tmp_path, capsys):
    root = tree(tmp_path, [OLD])
    before = snapshot(root)
    other = tmp_path / "other"
    other.mkdir()
    code = hk.main([str(other), "--extra", str(root), "--apply",
                    "--log", str(tmp_path / "x.jsonl")], now=NOW)
    assert code == 2 and snapshot(root) == before
    assert "holds report runs" in capsys.readouterr().err


@needs_ffmpeg
def test_probing_stops_planning_once_the_limit_is_reached(tmp_path, monkeypatch):
    root = tree(tmp_path, [OLD])
    rec = root / OLD / "hovi" / "recordings"
    for i in range(200):
        (rec / f"{i:03}.mp4").write_bytes(b"x")
    probed = []

    def fake(p):
        probed.append(p)
        return (1612, 1.0)

    monkeypatch.setattr(hk, "probe_video", fake)
    tasks, _ = hk.plan_tree(root, NOW, 0, 2, 7, {"video"}, hk.new_budget(3))
    assert len(tasks) == 3 and len(probed) < 200
