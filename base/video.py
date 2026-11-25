"""Screen-recording helpers shared by the nightly's session-end shrink and housekeep_reports.py.

Standard library only: housekeep_reports.py imports it after a sys.path insert, and the framework
imports it from conftest.py. Nothing here raises into its caller from the shrink entry points.
"""
import json
import logging
import os
import shutil
import subprocess
import time
from pathlib import Path

logger = logging.getLogger(__name__)

TARGET_HEIGHT = 806
DURATION_TOLERANCE_S = 0.5
# Encoder threads for the session-end shrink. Set before -i (decoder) and after ENCODE (encoder);
# housekeep_reports.py leaves ENCODE alone so it can use every core.
THREADS = 2
# "-bf 0": with B-frames the mp4 muxer can drop the hold of a screen recording's last frame from the
# container duration (a unime recording: 10.54 s became 9.30 s with all 102 frames intact), which
# fails the duration check (6 of 40 sampled recordings). Without B-frames all 40 passed; the files
# are ~9% larger but still ~98% smaller than the originals.
# "-enc_time_base demux": left to itself the encoder derives a time base from the odd frame rates
# of screen recordings (121/6 fps gave 1/98010000). Long recordings then overflow the mp4's 32-bit
# timestamps and the muxer aborts ("Invalid argument"), and others drift past the duration
# tolerance. The source's own time base keeps every timestamp exact.
ENCODE = ["-map", "0:v:0", "-an", "-fps_mode", "passthrough", "-vf", f"scale=-2:{TARGET_HEIGHT}",
          "-c:v", "libx264", "-preset", "slow", "-crf", "30", "-bf", "0",
          "-enc_time_base", "demux", "-pix_fmt", "yuv420p", "-f", "mp4"]


def probe_video(path):
    """(height, duration) of the first video stream, or None when ffprobe cannot read it."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=height:format=duration", "-of", "json", str(path)],
        capture_output=True, text=True)
    if out.returncode:
        return None
    try:
        info = json.loads(out.stdout)
        return int(info["streams"][0]["height"]), float(info["format"]["duration"])
    except (KeyError, IndexError, ValueError):
        return None


def count_frames(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0", "-show_entries",
         "stream=nb_read_frames", "-of", "csv=p=0", str(path)], capture_output=True, text=True)
    try:
        return int(out.stdout.strip().rstrip(","))
    except ValueError:
        return None


def verify_video(src, tmp):
    """Same frame count, duration within tolerance, and the target height."""
    before, after = probe_video(src), probe_video(tmp)
    if not before or not after or after[0] != TARGET_HEIGHT:
        return False
    if abs(before[1] - after[1]) > DURATION_TOLERANCE_S:
        return False
    frames = count_frames(src)
    return frames is not None and frames == count_frames(tmp)


def _fmt_mb(n):
    return f"{n / 1e6:.1f} MB" if n < 1e8 else f"{n / 1e6:.0f} MB"


def encode_command(src, tmp, threads=THREADS):
    """Low-priority ffmpeg call that shrinks `src` into `tmp` using `threads` threads."""
    t = ["-threads", str(threads)]
    return ["nice", "-n", "19", "ffmpeg", "-nostdin", "-v", "error", "-y", *t, "-i", str(src),
            *ENCODE, *t, str(tmp)]


def shrink_recording(path):
    """Re-encode one recording in place. Returns (outcome, before, after, reason).

    outcome is "shrunk", "kept" or "failed"; the original survives anything but "shrunk".
    Never raises (KeyboardInterrupt and SystemExit excepted).
    """
    path = Path(path)
    tmp = path.with_name(f"{path.stem}.{os.getpid()}.tmp.mp4")
    before = 0
    try:
        before = path.stat().st_size
        info = probe_video(path)
        if not info:
            return "failed", before, before, "could not read the video"
        if info[0] <= TARGET_HEIGHT:
            return "kept", before, before, "already small"
        timeout = max(60, 4 * info[1])
        if subprocess.run(encode_command(path, tmp), capture_output=True,
                          timeout=timeout).returncode:
            return "failed", before, before, "ffmpeg exited non-zero"
        if not verify_video(path, tmp):
            return "failed", before, before, "verification failed"
        after = tmp.stat().st_size
        if after >= before:
            return "kept", before, before, "result not smaller"
        st = path.stat()
        os.utime(tmp, ns=(st.st_atime_ns, st.st_mtime_ns))
        os.replace(tmp, path)
        return "shrunk", before, after, ""
    except subprocess.TimeoutExpired:
        return "failed", before, before, "ffmpeg timed out"
    except Exception as e:
        return "failed", before, before, f"{type(e).__name__}: {e}"
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def shrink_run_recordings(run_dir, enabled=True):
    """Shrink every recordings/*.mp4 of a wallet's run folder, logging one line per file.

    Never raises. With ffmpeg or ffprobe missing the files stay at full size, said once.
    """
    if not enabled:
        return
    try:
        files = sorted(f for f in (Path(run_dir) / "recordings").glob("*.mp4")
                       if not f.name.endswith(".tmp.mp4") and f.is_file())
        if not files:
            return
        if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
            logger.warning("[recording] ffmpeg or ffprobe not found: recordings kept at full size")
            return
        started = time.monotonic()
        count = total_before = total_after = 0
        for f in files:
            outcome, before, after, reason = shrink_recording(f)
            if outcome == "shrunk":
                count += 1
                total_before += before
                total_after += after
                logger.info(f"[recording] Re-encoded {f.name}: {_fmt_mb(before)} -> "
                            f"{_fmt_mb(after)}")
            else:
                level = logging.WARNING if outcome == "failed" else logging.INFO
                logger.log(level, f"[recording] Kept original {f.name}: {reason}")
        if count:
            logger.info(f"[recording] Shrank {count} recording(s): {_fmt_mb(total_before)} -> "
                        f"{_fmt_mb(total_after)} in {time.monotonic() - started:.0f} s")
    except Exception as e:
        logger.warning(f"[recording] Could not shrink recordings: {e}")
