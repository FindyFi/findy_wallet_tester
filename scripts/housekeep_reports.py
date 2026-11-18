#!/usr/bin/env python3
"""Shrink old report runs to save disk. Dry-run unless --apply is given.

Phase A (logs, lossless): appium.log and logcat.log of finished runs older than 2 days become
`<name>.zst` (read them with `zstdcat` / `zstdgrep`). Phase B (video, lossy): recordings/*.mp4 of
runs older than 7 days are re-encoded to 806 px height (x264 crf 30, no audio, every frame kept).
Everything else stays as it is: summary.json, report.html, app.log, app_info.json, test.log,
screenshots and xml_dumps (the report generator and the evidence cited in code comments read
them as plain files). Packing whole runs is deliberately not offered.

Safety rules:
- A run's age comes from its folder name (YYYY-MM-DD_HH-MM-SS), never from mtime.
- The newest --keep-newest runs of each tree are never touched.
- A wallet folder is a finished run only if it holds report.html (older code writes no
  summary.json). Other wallet folders are skipped.
- --apply refuses to start (and stops between files) while ~/.local/state/llm/nightly.lock
  exists or a run_tests.py or pytest process is running. A dry-run only warns.
- Every output is written under a temp name, verified (zstd: `zstd -t` plus a sha256 of the
  decompressed stream against the original; mp4: equal frame count and duration within 0.5 s),
  renamed atomically over its destination, and only then is the original deleted. On any
  failure the original stays and the exit code is 1.
- Only files inside wallet folders are written. The nightly picks its run with `ls -dt
  reports/*/` (run folder mtime), so each run folder's mtime is restored if it changed.
- The action log (JSON lines) lives outside the report trees, by default
  ~/.local/state/housekeep_reports/actions.jsonl.

Already re-encoded recordings are recognised by their height: anything at or below 806 px is left
alone. That needs no sidecar file and survives copying a run. Leftover `*.zst.tmp` and
`*.tmp.mp4` files of a crashed earlier run are deleted at the start of --apply.

--extra takes one-off trees such as .troubleshooting/: every logcat*.log and appium*.log of at
least 1 MiB under it is compressed (no age rules; the explicit path is the consent).

When to run --apply: inside Fri 03:00 - Mon 22:00 (the nightly runs Mon-Thu 22:00 to about 03:00),
or on a weekday 03:00-22:00 with no local test runs. Start the long video pass on Friday morning
or at the weekend. --apply stops with exit 3 as soon as a test run or the nightly lock appears,
and only one --apply can run at a time. --limit is global: it caps the files per phase over all
trees together.

Usage: scripts/housekeep_reports.py [ROOT ...] [--extra PATH] [--apply] [--only logs|video]
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

RUN_NAME = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}$")
LOG_NAMES = ("appium.log", "logcat.log")
EXTRA_LOG = re.compile(r"^(logcat|appium).*\.log$")
EXTRA_MIN_BYTES = 1 << 20
TARGET_HEIGHT = 806
DURATION_TOLERANCE_S = 0.5
LOCK_FILE = Path.home() / ".local/state/llm/nightly.lock"
APPLY_LOCK = Path.home() / ".local/state/housekeep_reports/apply.lock"
PROBE_THREADS = 8
DEFAULT_LOG = Path.home() / ".local/state/housekeep_reports/actions.jsonl"
# macOS shows the venv python as ".../MacOS/Python", hence the case-insensitive match.
INTERPRETER = re.compile(r"^(python[\d.]*|pytest|py\.test)$", re.I)
TEST_ARG = re.compile(r"^(run_tests\.py|pytest|py\.test)$")
# Planning ratios, measured on samples: zstd on logs, 806p crf30 on recordings.
LOG_RATIO = 100
VIDEO_RATIO = 37
VIDEO_SPEED = 70  # seconds of video encoded per CPU second at -preset slow
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


@dataclass
class Task:
    phase: str  # "logs" or "video"
    path: Path
    run: Path | None  # run folder whose mtime must survive; None for --extra
    size: int
    duration: float = 0.0
    finish: bool = False  # a verified .zst already exists: only the original is left to delete


def run_age_days(name, now):
    """Age of a run folder from its name, or None when the name is not a timestamp."""
    if not RUN_NAME.match(name):
        return None
    return (now - datetime.strptime(name, "%Y-%m-%d_%H-%M-%S")).total_seconds() / 86400


def discover_runs(root):
    """Run folders of a tree, oldest first (names sort chronologically)."""
    return sorted(p for p in root.iterdir()
                  if p.is_dir() and not p.is_symlink() and RUN_NAME.match(p.name))


def finished_wallets(run):
    return [w for w in sorted(run.iterdir())
            if w.is_dir() and not w.is_symlink() and (w / "report.html").is_file()]


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


def new_budget(limit):
    """Remaining files per phase; shared by every tree so --limit is global."""
    return {"logs": limit or float("inf"), "video": limit or float("inf")}


def _log_tasks(wallet, run, budget, notes):
    tasks = []
    for name in LOG_NAMES:
        f = wallet / name
        if not f.is_file() or f.is_symlink() or budget["logs"] <= 0:
            continue
        zst = wallet / (name + ".zst")
        size = f.stat().st_size
        if not zst.exists():
            tasks.append(Task("logs", f, run, size))
        elif verify_zstd(f, zst):  # a crash between the rename and the unlink left both
            tasks.append(Task("logs", f, run, size, finish=True))
        else:
            notes.append((str(f), "a .zst exists but does not match the original"))
            continue
        budget["logs"] -= 1
    return tasks


def _probe_tasks(candidates, budget, notes):
    """Probe recordings in parallel, in chunks, and stop once the video budget is used up."""
    tasks = []
    with ThreadPoolExecutor(PROBE_THREADS) as pool:
        for i in range(0, len(candidates), PROBE_THREADS * 8):
            if budget["video"] <= 0:
                break
            chunk = candidates[i:i + PROBE_THREADS * 8]
            for (f, run), info in zip(chunk, pool.map(probe_video, [c[0] for c in chunk])):
                if info is None:
                    notes.append((str(f), "ffprobe cannot read it"))
                elif info[0] > TARGET_HEIGHT and budget["video"] > 0:  # else already re-encoded
                    tasks.append(Task("video", f, run, f.stat().st_size, info[1]))
                    budget["video"] -= 1
    return tasks


def plan_tree(root, now, keep_newest, logs_days, video_days, phases, budget=None):
    """Tasks plus skip notes for one report tree."""
    budget = budget or new_budget(0)
    runs = discover_runs(root)
    protected = {r.name for r in runs[max(0, len(runs) - keep_newest):]} if keep_newest else set()
    tasks, notes, candidates = [], [], []
    for run in runs:
        age = run_age_days(run.name, now)
        if run.name in protected:
            notes.append((run.name, "newest runs are protected"))
            continue
        for wallet in sorted(p for p in run.iterdir() if p.is_dir() and not p.is_symlink()):
            if not (wallet / "report.html").is_file():
                notes.append((f"{run.name}/{wallet.name}", "no report.html, not a finished run"))
                continue
            if "logs" in phases and age >= logs_days:
                tasks += _log_tasks(wallet, run, budget, notes)
            rec = wallet / "recordings"
            if "video" in phases and age >= video_days and not rec.is_symlink():
                candidates += [(f, run) for f in sorted(rec.glob("*.mp4"))
                               if not f.name.endswith(".tmp.mp4") and f.is_file()
                               and not f.is_symlink()]
    return tasks + _probe_tasks(candidates, budget, notes), notes


def plan_extra(path, budget=None):
    budget = budget or new_budget(0)
    tasks = []
    for f in sorted(path.rglob("*")):
        if (f.is_file() and not f.is_symlink() and EXTRA_LOG.match(f.name)
                and f.stat().st_size >= EXTRA_MIN_BYTES and budget["logs"] > 0
                and not f.with_name(f.name + ".zst").exists()):
            tasks.append(Task("logs", f, None, f.stat().st_size))
            budget["logs"] -= 1
    return tasks


def stale_temps(root):
    """Leftovers of a crashed run, inside wallet folders only."""
    found = []
    for run in discover_runs(root):
        for wallet in (p for p in run.iterdir() if p.is_dir() and not p.is_symlink()):
            found += list(wallet.glob("*.zst.tmp")) + list(wallet.glob("*.zst.*.tmp"))
            found += list((wallet / "recordings").glob("*.tmp.mp4"))
    return found


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _sha256_zstd(path):
    h = hashlib.sha256()
    proc = subprocess.Popen(["zstd", "-dc", str(path)], stdout=subprocess.PIPE)
    for chunk in iter(lambda: proc.stdout.read(1 << 20), b""):
        h.update(chunk)
    return proc.wait() == 0 and h.hexdigest()


def verify_zstd(src, tmp):
    """The temp file is a sound zstd stream that decompresses to exactly the original."""
    if subprocess.run(["zstd", "-t", "-q", str(tmp)], capture_output=True).returncode:
        return False
    return _sha256_zstd(tmp) == _sha256_file(src)


def verify_video(src, tmp):
    """Same frame count, duration within tolerance, and the target height."""
    before, after = probe_video(src), probe_video(tmp)
    if not before or not after or after[0] != TARGET_HEIGHT:
        return False
    if abs(before[1] - after[1]) > DURATION_TOLERANCE_S:
        return False
    frames = count_frames(src)
    return frames is not None and frames == count_frames(tmp)


def compress_log(src):
    dest = src.with_name(src.name + ".zst")
    tmp = src.with_name(f"{src.name}.zst.{os.getpid()}.tmp")
    return dest, tmp, ["zstd", "-9", "-T0", "-q", "-f", str(src), "-o", str(tmp)]


def encode_video(src):
    tmp = src.with_name(f"{src.stem}.{os.getpid()}.tmp.mp4")
    return src, tmp, ["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(src), *ENCODE, str(tmp)]


def process(task):
    """Convert one file. Returns (outcome, bytes_after, reason); the original survives a failure."""
    src = task.path
    if task.finish:  # the verified .zst is already in place
        src.unlink()
        return "done", src.with_name(src.name + ".zst").stat().st_size, "removed original"
    dest, tmp, cmd = compress_log(src) if task.phase == "logs" else encode_video(src)
    verify = verify_zstd if task.phase == "logs" else verify_video
    try:
        if subprocess.run(cmd, capture_output=True).returncode:
            return "failed", 0, "tool exited non-zero"
        if not verify(src, tmp):
            return "failed", 0, "verification failed"
        after = tmp.stat().st_size
        if after >= task.size:
            return "skipped", task.size, "result not smaller"
        st = src.stat()
        os.utime(tmp, ns=(st.st_atime_ns, st.st_mtime_ns))
        os.replace(tmp, dest)  # atomic; for video this replaces the original in one step
        if dest != src:
            src.unlink()
        return "done", after, ""
    finally:
        tmp.unlink(missing_ok=True)


def busy_reason(lock=None, ps_output=None):
    """Why --apply must not start now, or None."""
    lock = LOCK_FILE if lock is None else lock
    if lock.exists():
        return f"{lock} exists (a nightly run is active)"
    if ps_output is None:
        ps_output = subprocess.run(["ps", "-axo", "pid=,command="],
                                   capture_output=True, text=True).stdout
    for line in ps_output.splitlines():
        pid, _, command = line.strip().partition(" ")
        argv = [os.path.basename(a) for a in command.split()]
        # Only real python/pytest processes count: a shell whose -c string merely mentions
        # pytest is not a test run.
        if (pid != str(os.getpid()) and argv and INTERPRETER.match(argv[0])
                and any(TEST_ARG.match(a) for a in argv[1:] + argv[:1])):
            return f"a test process is running: {command[:100]}"
    return None


def _log_action(fh, **fields):
    if fh:
        fh.write(json.dumps({"ts": datetime.now().isoformat(timespec="seconds"), **fields}) + "\n")
        fh.flush()


def human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1000 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1000


class Busy(Exception):
    """A test run or the nightly appeared while --apply was working."""


def execute(tasks, apply, log_fh):
    """Run (or count) the tasks. Returns per-phase stats and the failure count."""
    stats = {p: {"files": 0, "before": 0, "after": 0, "seconds": 0.0} for p in ("logs", "video")}
    failures = 0
    mtimes = {}
    try:
        for task in tasks:
            s = stats[task.phase]
            s["files"] += 1
            s["before"] += task.size
            s["seconds"] += task.duration / VIDEO_SPEED
            if not apply:
                s["after"] += task.size // (LOG_RATIO if task.phase == "logs" else VIDEO_RATIO)
                continue
            busy = busy_reason()
            if busy:
                raise Busy(f"{busy}; stopped before {task.path}")
            if task.run is not None and task.run not in mtimes:
                mtimes[task.run] = task.run.stat().st_mtime_ns
            try:
                outcome, after, reason = process(task)
            except OSError as exc:  # the original is only removed after the replace
                outcome, after, reason = "failed", 0, f"OSError: {exc}"
            if outcome == "failed":
                failures += 1
            s["after"] += after if outcome == "done" else task.size
            _log_action(log_fh, phase=task.phase, path=str(task.path.resolve()),
                        outcome=outcome, bytes_before=task.size, bytes_after=after,
                        reason=reason)
            print(f"{outcome:7} {task.phase:5} {task.path} {reason}", flush=True)
    finally:
        for run, mtime in mtimes.items():
            if run.stat().st_mtime_ns != mtime:
                os.utime(run, ns=(run.stat().st_atime_ns, mtime))
    return stats, failures


def _holds_runs(path):
    """True when the path is, is inside, or directly holds run-named folders."""
    path = path.resolve()
    return (any(RUN_NAME.match(part) for part in path.parts)
            or any(RUN_NAME.match(p.name) for p in path.iterdir()))


def main(argv=None, now=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("roots", nargs="*", type=Path, default=[Path("reports")])
    ap.add_argument("--extra", action="append", type=Path, default=[],
                    help="one-off tree (not a reports root: no age rules apply to it)")
    ap.add_argument("--apply", action="store_true", help="change files (default is a dry-run)")
    ap.add_argument("--only", choices=("logs", "video"))
    ap.add_argument("--keep-newest", type=int, default=4)
    ap.add_argument("--logs-min-age-days", type=float, default=2)
    ap.add_argument("--video-min-age-days", type=float, default=7)
    ap.add_argument("--limit", type=int, default=0,
                    help="max files per phase over all trees together (0 = all)")
    ap.add_argument("--log", type=Path, default=DEFAULT_LOG)
    args = ap.parse_args(argv)
    if args.keep_newest < 0 or args.limit < 0:
        ap.error("--keep-newest and --limit must not be negative")
    now = now or datetime.now()
    for tool in ("zstd", "ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            print(f"missing tool: {tool}", file=sys.stderr)
            return 2
    for tree in [*args.roots, *args.extra]:
        if not tree.is_dir():
            print(f"not a directory: {tree}", file=sys.stderr)
            return 2
    for tree in args.extra:
        if _holds_runs(tree):
            print(f"--extra {tree} holds report runs; pass it as a root instead", file=sys.stderr)
            return 2
    busy = busy_reason()
    if busy and args.apply:
        print(f"refusing to run: {busy}", file=sys.stderr)
        return 3
    if busy:
        print(f"warning (dry-run continues, nothing is changed): {busy}", file=sys.stderr)
    log_fh = lock_fh = None
    if args.apply:
        APPLY_LOCK.parent.mkdir(parents=True, exist_ok=True)
        lock_fh = open(APPLY_LOCK, "w")
        try:
            fcntl.flock(lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            print("refusing to run: another --apply is in progress", file=sys.stderr)
            return 3
        args.log.parent.mkdir(parents=True, exist_ok=True)
        log_fh = open(args.log, "a")
    phases = {args.only} if args.only else {"logs", "video"}
    budget = new_budget(args.limit)
    failures = 0
    try:
        for root in args.roots:
            if args.apply:
                for leftover in stale_temps(root):
                    leftover.unlink()
                    _log_action(log_fh, phase="cleanup", path=str(leftover.resolve()),
                                outcome="deleted")
            tasks, notes = plan_tree(root, now, args.keep_newest, args.logs_min_age_days,
                                     args.video_min_age_days, phases, budget)
            failures += report(f"{root}", tasks, notes, args, log_fh)
        for path in args.extra:
            failures += report(f"{path} (extra)", plan_extra(path, budget), [], args, log_fh)
    except Busy as exc:
        print(f"stopping: {exc}", file=sys.stderr)
        return 3
    finally:
        if log_fh:
            log_fh.close()
        if lock_fh:
            lock_fh.close()
    return 1 if failures else 0


def report(label, tasks, notes, args, log_fh):
    shown = [(what, why) for what, why in notes if "protected" not in why]
    for what, why in shown:
        print(f"   note: {what}: {why}")
        _log_action(log_fh, phase="note", path=what, outcome="skipped", reason=why)
    stats, failures = execute(tasks, args.apply, log_fh)
    verb = "applied" if args.apply else "dry-run"
    print(f"== {label} [{verb}]  skipped: {len(notes)} ({len(notes) - len(shown)} protected)")
    for phase, s in stats.items():
        if not s["files"]:
            continue
        extra = "" if phase == "logs" else f", about {s['seconds'] / 60:.0f} CPU-min to encode"
        tail = "" if args.apply else " (estimated)"
        print(f"   {phase:5} {s['files']} files  {human(s['before'])} -> {human(s['after'])}{tail}"
              f"{extra}")
    if failures:
        print(f"   FAILURES: {failures} (originals kept)")
    return failures


if __name__ == "__main__":
    sys.exit(main())
