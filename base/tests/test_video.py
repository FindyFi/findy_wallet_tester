"""base/video.py: the session-end shrink of a wallet's recordings, and the shared constants.

Real-ffmpeg tests use tiny synthetic clips under tmp_path; nothing reads the real reports/.
"""
import importlib.util
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from base import video

ROOT = Path(__file__).resolve().parents[2]
_SPEC = importlib.util.spec_from_file_location("housekeep_reports_v", ROOT / "scripts" / "housekeep_reports.py")
hk = importlib.util.module_from_spec(_SPEC)
sys.modules["housekeep_reports_v"] = hk
_SPEC.loader.exec_module(hk)

root_conftest = importlib.import_module("conftest")

needs_ffmpeg = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
                                  reason="ffmpeg missing")


def make_rec(tmp_path, name="a.mp4", size=1000):
    rec = tmp_path / "recordings"
    rec.mkdir(parents=True, exist_ok=True)
    f = rec / name
    f.write_bytes(b"x" * size)
    return f


class FakeFfmpeg:
    """Stands in for subprocess.run inside base.video; writes `out_size` bytes to the output."""

    def __init__(self, returncode=0, out_size=100, raises=None):
        self.calls, self.returncode, self.out_size, self.raises = [], returncode, out_size, raises

    def __call__(self, cmd, **kw):
        self.calls.append((cmd, kw))
        Path(cmd[-1]).write_bytes(b"y" * self.out_size)
        if self.raises:
            raise self.raises
        return SimpleNamespace(returncode=self.returncode, stdout="", stderr="")


@pytest.fixture
def patched(monkeypatch):
    def _patch(fake, height=1920, verify=True, duration=10.0):
        monkeypatch.setattr(video.subprocess, "run", fake)
        monkeypatch.setattr(video, "probe_video", lambda p: (height, duration))
        monkeypatch.setattr(video, "verify_video", lambda s, t: verify)
        return fake
    return _patch


def test_shrink_replaces_in_place_without_leaving_a_temp(tmp_path, patched):
    f = make_rec(tmp_path)
    fake = patched(FakeFfmpeg())
    outcome, before, after, _ = video.shrink_recording(f)
    assert (outcome, before, after) == ("shrunk", 1000, 100)
    assert f.read_bytes() == b"y" * 100
    assert [p.name for p in f.parent.iterdir()] == ["a.mp4"]
    assert fake.calls[0][0][-1].endswith(".tmp.mp4")


def test_the_encode_has_a_timeout_and_the_original_keeps_its_mtime(tmp_path, patched):
    f = make_rec(tmp_path)
    os.utime(f, (1_000_000_000, 1_000_000_000))
    fake = patched(FakeFfmpeg(), duration=10.0)
    assert video.shrink_recording(f)[0] == "shrunk"
    assert fake.calls[0][1]["timeout"] == 60
    assert f.stat().st_mtime == 1_000_000_000


def test_ffmpeg_failures_keep_the_original_and_do_not_raise(tmp_path, patched):
    for fake in (FakeFfmpeg(returncode=1), FakeFfmpeg(raises=subprocess.TimeoutExpired("ffmpeg", 1)),
                 FakeFfmpeg(raises=FileNotFoundError("ffmpeg")), FakeFfmpeg(raises=OSError("x"))):
        f = make_rec(tmp_path)
        patched(fake)
        assert video.shrink_recording(f)[0] == "failed"
        assert f.read_bytes() == b"x" * 1000
        assert [p.name for p in f.parent.iterdir()] == ["a.mp4"]


def test_a_failed_verification_keeps_the_original(tmp_path, patched):
    f = make_rec(tmp_path)
    patched(FakeFfmpeg(), verify=False)
    assert video.shrink_recording(f)[0] == "failed"
    assert f.read_bytes() == b"x" * 1000
    assert [p.name for p in f.parent.iterdir()] == ["a.mp4"]


def test_a_result_that_is_not_smaller_keeps_the_original(tmp_path, patched):
    f = make_rec(tmp_path)
    patched(FakeFfmpeg(out_size=2000))
    assert video.shrink_recording(f)[0] == "kept"
    assert f.read_bytes() == b"x" * 1000
    assert [p.name for p in f.parent.iterdir()] == ["a.mp4"]


def test_an_already_small_recording_is_not_encoded(tmp_path, patched):
    f = make_rec(tmp_path)
    fake = patched(FakeFfmpeg(), height=video.TARGET_HEIGHT)
    assert video.shrink_recording(f)[0] == "kept"
    assert fake.calls == []


def test_the_command_is_niced_and_limits_threads_on_both_sides():
    cmd = video.encode_command("in.mp4", "out.mp4")
    assert cmd[:3] == ["nice", "-n", "19"]
    n = str(video.THREADS)
    i = cmd.index("-i")
    assert cmd[i - 2:i] == ["-threads", n], "input side limits only the decoder"
    assert cmd[-3:-1] == ["-threads", n], "output side limits the encoder"
    assert "-threads" not in video.ENCODE, "housekeep_reports.py wants every core"
    assert video.THREADS == 2


def test_encode_pins_the_settings_that_keep_durations_exact():
    e = video.ENCODE
    assert e[e.index("-bf") + 1] == "0"
    assert e[e.index("-enc_time_base") + 1] == "demux"


def test_the_housekeeping_script_shares_the_constants():
    assert hk.ENCODE is video.ENCODE
    assert hk.TARGET_HEIGHT == video.TARGET_HEIGHT == 806
    assert hk.verify_video is video.verify_video


def test_the_temp_name_is_one_housekeeping_sweeps(tmp_path, patched):
    run = tmp_path / "2026-11-01_10-00-00"
    wallet = run / "w"
    f = make_rec(wallet)
    fake = patched(FakeFfmpeg(raises=KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        video.shrink_recording(f)
    assert [p.name for p in f.parent.iterdir()] == ["a.mp4"], "the finally removes the temp"
    # the name it used is one stale_temps globs for
    tmp = Path(fake.calls[0][0][-1])
    tmp.write_bytes(b"z")
    assert tmp in hk.stale_temps(tmp_path)


def test_disabled_makes_no_calls(tmp_path, patched):
    make_rec(tmp_path)
    fake = patched(FakeFfmpeg())
    video.shrink_run_recordings(tmp_path, enabled=False)
    assert fake.calls == []


def test_run_shrinks_every_file_logs_each_and_a_total(tmp_path, patched, caplog, monkeypatch):
    make_rec(tmp_path, "a.mp4")
    make_rec(tmp_path, "a.attempt1.mp4")
    make_rec(tmp_path, "stale.123.tmp.mp4")
    fake = patched(FakeFfmpeg())
    monkeypatch.setattr(video.shutil, "which", lambda n: "/bin/" + n)
    with caplog.at_level(logging.INFO, logger="base.video"):
        video.shrink_run_recordings(tmp_path)
    assert len(fake.calls) == 2
    msgs = [r.message for r in caplog.records]
    assert sum("Re-encoded" in m for m in msgs) == 2
    assert any("Shrank 2 recording(s)" in m for m in msgs)


def test_a_missing_ffmpeg_says_so_once_and_changes_nothing(tmp_path, patched, caplog, monkeypatch):
    f = make_rec(tmp_path)
    fake = patched(FakeFfmpeg())
    monkeypatch.setattr(video.shutil, "which", lambda n: None)
    video.shrink_run_recordings(tmp_path)
    assert fake.calls == [] and f.read_bytes() == b"x" * 1000
    assert sum("full size" in r.message for r in caplog.records) == 1


# --- wiring in pytest_sessionfinish -----------------------------------------------------------

def _finish(config, status=0):
    root_conftest.pytest_sessionfinish(SimpleNamespace(config=config), status)


def _spy(monkeypatch):
    calls = []
    monkeypatch.setattr(root_conftest, "shrink_run_recordings",
                        lambda run_dir, enabled: calls.append((run_dir, enabled)))
    return calls


def test_session_finish_passes_the_stashed_flag(tmp_path, monkeypatch):
    calls = _spy(monkeypatch)
    _finish(SimpleNamespace(_run_dir=tmp_path, _shrink_recordings=True))
    assert calls == [(tmp_path, True)]


def test_session_finish_without_a_stash_makes_no_encode_call(tmp_path, monkeypatch, patched):
    make_rec(tmp_path)
    fake = patched(FakeFfmpeg())
    monkeypatch.setattr(video.shutil, "which", lambda n: "/bin/" + n)
    _finish(SimpleNamespace(_run_dir=tmp_path))
    assert fake.calls == []


def test_an_interrupted_session_does_not_shrink(tmp_path, monkeypatch):
    calls = _spy(monkeypatch)
    _finish(SimpleNamespace(_run_dir=tmp_path, _shrink_recordings=True),
            pytest.ExitCode.INTERRUPTED)
    assert calls == []


def test_a_failing_shrink_does_not_stop_the_handler_detach(tmp_path, monkeypatch):
    def boom(run_dir, enabled):
        raise RuntimeError("boom")
    monkeypatch.setattr(root_conftest, "shrink_run_recordings", boom)
    handler = logging.FileHandler(tmp_path / "test.log")
    config = SimpleNamespace(_run_dir=tmp_path, _shrink_recordings=True, _log_handler=handler)
    logging.getLogger().addHandler(handler)
    try:
        _finish(config)
        assert config._log_handler is None
        assert handler not in logging.getLogger().handlers
    finally:
        logging.getLogger().removeHandler(handler)
        handler.close()


def test_the_log_lines_land_in_the_wallets_test_log(tmp_path, patched, monkeypatch):
    make_rec(tmp_path)
    patched(FakeFfmpeg())
    monkeypatch.setattr(video.shutil, "which", lambda n: "/bin/" + n)
    handler = logging.FileHandler(tmp_path / "test.log")
    handler.setLevel(logging.INFO)
    root = logging.getLogger()
    old = root.level
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    try:
        _finish(SimpleNamespace(_run_dir=tmp_path, _shrink_recordings=True, _log_handler=handler))
    finally:
        root.setLevel(old)
        root.removeHandler(handler)
        handler.close()
    text = (tmp_path / "test.log").read_text()
    assert "Re-encoded a.mp4" in text and "Shrank 1 recording(s)" in text


# --- the real thing ---------------------------------------------------------------------------

def _clip(path, height):
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i",
                    f"testsrc=size=270x{height}:rate=10:duration=2", "-c:v", "libx264", "-qp", "0",
                    "-pix_fmt", "yuv420p", str(path)], check=True)


@needs_ffmpeg
def test_real_ffmpeg_shrinks_to_the_target_height_with_every_frame(tmp_path):
    f = make_rec(tmp_path)
    _clip(f, 1080)
    frames = video.count_frames(f)
    outcome, before, after, reason = video.shrink_recording(f)
    assert outcome == "shrunk", reason
    assert video.probe_video(f)[0] == video.TARGET_HEIGHT
    assert video.count_frames(f) == frames
    assert [p.name for p in f.parent.iterdir()] == ["a.mp4"]


@needs_ffmpeg
def test_real_ffmpeg_leaves_a_small_clip_alone(tmp_path):
    f = make_rec(tmp_path)
    _clip(f, 400)
    data = f.read_bytes()
    assert video.shrink_recording(f)[0] == "kept"
    assert f.read_bytes() == data
