"""Run the wallet test suites in parallel, one worker per device.

    python runners/run_parallel.py [wallet ...] [--wait SECONDS] [--allow-shared-server] [--dry-run]
                                   [-- extra pytest arguments]

Two kinds of wallet, decided by `.env`:

  pinned    `<WALLET>_DEVICE_NAME` is set (not blank). The wallet runs only on that device. If the
            device or its Appium server is not ready within --wait, the wallet is skipped.
  floating  no per-wallet device. Floating wallets share one queue and any ready device takes the
            next one. A device that does not answer simply takes no work.

The device pool is every device the configuration names: the pinned wallets' devices plus
DEVICE_NAME. A floating wallet only runs on a device whose screen-lock PIN the runner can work out
from `.env` without guessing (see `build_plan`). Each wallet is one `pytest` child process, so
everything it writes lands in reports/<timestamp>/<wallet>/ exactly as with run_tests.py, plus a
`pytest.out` with its console output.

Exit code: 130 after an interrupt; else the highest pytest code other than 0, 1 and 5; else 10 when
a wallet was skipped; else the highest code (0, 1 or 5).
"""
import argparse
import json
import os
import signal
import subprocess
import sys
import threading
import time
import urllib.request
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from base.config import _expand_env, load_config  # noqa: E402  (importing it loads .env)
from base.utils import TIMESTAMP_FORMAT, list_wallets  # noqa: E402

DEFAULT_WAIT = 30.0
DEFAULT_BUSY_WAIT = 120.0  # Appium's own logcat can outlive a session by about its 60 s timeout
POLL_SECONDS = 5.0       # between probes while waiting for a device
TICK_SECONDS = 0.5       # between checks on a running child
GRACE_SECONDS = 150.0    # after SIGINT: the crash-store capture at session end can take 120 s
EXIT_SKIPPED = 10
EXIT_INTERRUPTED = 130
UNREMARKABLE = (0, 1, 5)  # pytest: all passed / some failed / nothing collected


# --- planning -------------------------------------------------------------------------------

@dataclass
class Pinned:
    wallet: str
    server: str


@dataclass
class Device:
    """One device in the pool. Holds only the NAME of the variable a PIN comes from, never a PIN."""
    serial: str
    name: str
    server: str
    default: bool = False
    pin_vars: List[str] = field(default_factory=list)
    pin_missing: bool = False
    pin_var: Optional[str] = None
    pin_note: str = ""
    server_ambiguous: bool = False
    pinned: List[Pinned] = field(default_factory=list)

    @property
    def takes_floating(self) -> bool:
        return bool(self.pin_var) and not self.server_ambiguous


@dataclass
class Plan:
    wallets: List[str]
    devices: Dict[str, Device]
    floating: List[str]
    unresolved: List[Tuple[str, str]]


def normalize_server(url: str) -> str:
    """For comparing servers only: no trailing slash, localhost spelled as 127.0.0.1."""
    return url.strip().rstrip("/").replace("//localhost", "//127.0.0.1").lower()


def _nonblank(env, key: str) -> bool:
    return bool((env.get(key) or "").strip())


def resolve_target(wallet: str, load: Callable = load_config) -> Tuple[str, str, str]:
    """(udid, device_name, server) the way conftest._resolve_device sees a single-process run."""
    config = load(wallet)
    android = config["android"]
    devices = android.get("devices")
    if devices:
        device = devices[0]
        name = device["device_name"]
        return device.get("udid", name), name, device["server"]
    name = android["device_name"]
    return android.get("udid", name), name, config["server"]


def default_server() -> str:
    """The shared server, expanded from config/device.json the same way a wallet config is."""
    raw = json.loads((REPO_ROOT / "config" / "device.json").read_text())["server"]
    return _expand_env(raw, [], wallet="")


def build_plan(wallets: List[str], env, load: Callable, server_default: str) -> Plan:
    """Sort the wallets into pinned (per device) and floating, and build the device pool.

    A device's PIN counts as known when every source variable is non-blank and they agree: the
    pinned wallets' own `<WALLET>_DEVICE_PIN`, and DEVICE_PIN for the default device. The value
    `load` would return is never used, because it falls back to the default device's PIN.
    """
    devices: Dict[str, Device] = {}
    floating: List[str] = []
    unresolved: List[Tuple[str, str]] = []
    default_name = (env.get("DEVICE_NAME") or "").strip()
    if default_name:
        devices[default_name] = Device(default_name, default_name, server_default, default=True)
        if _nonblank(env, "DEVICE_PIN"):
            devices[default_name].pin_vars.append("DEVICE_PIN")
    for wallet in wallets:
        prefix = wallet.upper()
        if not _nonblank(env, f"{prefix}_DEVICE_NAME"):
            floating.append(wallet)
            continue
        try:
            udid, name, server = resolve_target(wallet, load)
        except Exception as e:  # a broken config must skip this wallet, never stop the run
            first_line = str(e).strip().splitlines()[0] if str(e).strip() else ""
            unresolved.append((wallet, f"its configuration could not be read "
                                       f"({type(e).__name__}: {first_line})"))
            continue
        device = devices.setdefault(udid, Device(udid, name, server))
        if not device.pinned:
            device.server = server  # what its pinned wallets say beats the default server
        elif normalize_server(server) != normalize_server(device.server):
            device.server_ambiguous = True
        device.pinned.append(Pinned(wallet, server))
        own = f"{prefix}_DEVICE_PIN"
        if _nonblank(env, own):
            if own not in device.pin_vars:
                device.pin_vars.append(own)
        elif not device.default:
            device.pin_missing = True
    for device in devices.values():
        if device.pin_missing:
            device.pin_note = "a pinned wallet on it has no <WALLET>_DEVICE_PIN"
        elif not device.pin_vars:
            device.pin_note = "no PIN variable is set for it"
        elif len({env[v] for v in device.pin_vars}) > 1:
            device.pin_note = "its PIN variables disagree"
        else:
            device.pin_var = device.pin_vars[0]
    return Plan(list(wallets), devices, floating, unresolved)


def shared_servers(plan: Plan) -> Dict[str, List[str]]:
    """normalized server -> serials, for the servers more than one device uses."""
    groups: Dict[str, List[str]] = {}
    for device in plan.devices.values():
        groups.setdefault(normalize_server(device.server), []).append(device.serial)
    return {s: serials for s, serials in groups.items() if len(serials) > 1}


def describe_plan(plan: Plan) -> List[str]:
    lines = ["Dry run: nothing was probed, started or created.", "", "Device pool:"]
    shared = shared_servers(plan)
    for device in plan.devices.values():
        others = [s for s in shared.get(normalize_server(device.server), []) if s != device.serial]
        notes = [f"server {device.server}"]
        if others:
            notes.append(f"shares the server with {', '.join(others)}, so they take turns")
        notes.append("PIN known: yes" if device.pin_var else f"PIN known: no ({device.pin_note})")
        if device.server_ambiguous:
            notes.append("server ambiguous: its pinned wallets name different servers")
        if not device.takes_floating:
            notes.append("takes no floating wallets")
        lines.append(f"  {device.serial}: " + "; ".join(notes))
    if not plan.devices:
        lines.append("  (none: set DEVICE_NAME or a <WALLET>_DEVICE_NAME)")
    lines += ["", "Pinned wallets (run only on their device):"]
    pinned_any = False
    for device in plan.devices.values():
        if device.pinned:
            pinned_any = True
            lines.append(f"  {device.serial}: {', '.join(p.wallet for p in device.pinned)}")
    if not pinned_any:
        lines.append("  (none)")
    lines += ["", "Floating queue (the first ready device takes the next):",
              f"  {', '.join(plan.floating) if plan.floating else '(none)'}"]
    if plan.unresolved:
        lines += ["", "Cannot run:"] + [f"  {w}: {why}" for w, why in plan.unresolved]
    return lines


# --- probes ---------------------------------------------------------------------------------

def probe_device_adb(serial: str) -> Tuple[bool, str]:
    try:
        out = subprocess.run(["adb", "-s", serial, "get-state"], capture_output=True, text=True,
                             timeout=5)
    except Exception as e:
        return False, f"adb failed: {e}"
    text = (out.stdout or out.stderr or "").strip()
    if out.returncode != 0 or text != "device":
        return False, (text or f"adb exited {out.returncode}")
    try:
        boot = subprocess.run(["adb", "-s", serial, "shell", "getprop", "sys.boot_completed"],
                              capture_output=True, text=True, timeout=5)
    except Exception as e:
        return False, f"adb failed: {e}"
    if (boot.stdout or "").strip() != "1":
        return False, "still booting"
    return True, "device"


def probe_server_http(url: str) -> Tuple[bool, str]:
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/status", timeout=3) as response:
            body = json.loads(response.read().decode() or "{}")
        value = body.get("value") if isinstance(body, dict) else None
        if isinstance(value, dict) and value.get("ready") is False:
            return False, "the server says it is not ready"
    except Exception as e:
        return False, str(e)
    return True, "ready"


def list_processes() -> str:
    try:
        return subprocess.run(["ps", "-eo", "pid=,ppid=,args="], capture_output=True, text=True,
                              timeout=10).stdout or ""
    except Exception:
        return ""


def busy_logcats(ps_text: str, serial: str) -> List[int]:
    """Live `adb -s <serial> logcat` captures. One whose parent is init was orphaned by an
    interrupted run, belongs to nobody and is swept by the next session, so it is not busy."""
    pids = []
    for line in ps_text.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) < 3 or not parts[0].isdigit() or not parts[1].isdigit():
            continue
        argv = parts[2].split()
        if os.path.basename(argv[0]) != "adb" or "logcat" not in argv[1:]:
            continue
        if any(a == "-s" and nxt == serial for a, nxt in zip(argv, argv[1:])) and parts[1] != "1":
            pids.append(int(parts[0]))
    return pids


# --- running --------------------------------------------------------------------------------

def make_session_dir(wall: Callable, root: Path = REPO_ROOT) -> Path:
    moment = wall()
    while True:
        session = root / "reports" / moment.strftime(TIMESTAMP_FORMAT)
        try:
            session.mkdir(parents=True)
            return session
        except FileExistsError:
            moment += timedelta(seconds=1)


def print_line(line: str) -> None:
    """Console output that never raises: a closed pipe (`| head`) must not end supervision."""
    try:
        print(line, flush=True)
    except (OSError, RuntimeError, ValueError):
        pass


def install_signals(stop: threading.Event, force: threading.Event) -> None:
    def handler(signum, frame):
        if stop.is_set():
            force.set()
            print_line("Second interrupt: killing the running tests now.")
        else:
            stop.set()
            print_line("Interrupted: asking each running test to stop (press Ctrl-C again to "
                       "kill them). Wallets that have not started will not start.")
    signals = [signal.SIGINT, signal.SIGTERM]
    if signal.getsignal(signal.SIGHUP) != signal.SIG_IGN:  # keep nohup working
        signals.append(signal.SIGHUP)
    for sig in signals:
        signal.signal(sig, handler)


@dataclass
class Ctx:
    """Everything the scheduler touches outside itself, so tests can replace all of it."""
    session_dir: Callable = field(default=lambda: make_session_dir(datetime.now))
    popen: Callable = subprocess.Popen
    probe_device: Callable = probe_device_adb
    probe_server: Callable = probe_server_http
    list_processes: Callable = list_processes
    sleep: Callable = time.sleep
    clock: Callable = time.monotonic
    wall: Callable = datetime.now
    out: Callable = print_line
    kill: Callable = os.kill
    killpg: Callable = os.killpg
    install_signals: Callable = install_signals
    environ: object = os.environ
    repo_root: Path = REPO_ROOT
    python: str = sys.executable
    wait: float = DEFAULT_WAIT
    busy_wait: float = DEFAULT_BUSY_WAIT
    allow_shared_server: bool = False
    extras: List[str] = field(default_factory=list)


@dataclass
class Result:
    wallet: str
    device: str = "-"
    state: str = "not started"  # done | killed | skipped | not started
    code: Optional[int] = None
    seconds: float = 0.0
    reason: str = ""


def fmt_duration(seconds: float) -> str:
    s = int(seconds)
    if s >= 3600:
        return f"{s // 3600}h{s % 3600 // 60:02d}m{s % 60:02d}s"
    return f"{s // 60}m{s % 60:02d}s" if s >= 60 else f"{s}s"


def exit_code(results: List[Result], interrupted: bool) -> int:
    if interrupted:
        return EXIT_INTERRUPTED
    codes = [r.code for r in results if r.state in ("done", "killed") and r.code is not None]
    notable = [c for c in codes if c not in UNREMARKABLE]
    if notable:
        return max(notable)
    if any(r.state in ("skipped", "not started") for r in results):
        return EXIT_SKIPPED
    return max(codes) if codes else 0


class Scheduler:
    def __init__(self, plan: Plan, ctx: Ctx):
        self.plan, self.ctx = plan, ctx
        self.lock = threading.Lock()  # results, console, the floating queue, device states
        self.stop, self.force = threading.Event(), threading.Event()
        self.floating = deque(plan.floating)
        self.results: Dict[str, Result] = {w: Result(w) for w in plan.wallets}
        self.eligible = {s for s, d in plan.devices.items() if d.takes_floating}
        self.state = {s: "unknown" for s in self.eligible}  # unknown | alive | dead
        self.all_dead_since: Optional[float] = None
        self.session: Optional[Path] = None
        self.server_locks: Dict[str, threading.Lock] = {}
        if not ctx.allow_shared_server:
            self.server_locks = {normalize_server(d.server): threading.Lock()
                                 for d in plan.devices.values()}

    # -- output and results

    def say(self, line: str) -> None:
        with self.lock:
            try:
                self.ctx.out(f"[{self.ctx.wall().strftime('%H:%M:%S')}] {line}")
            except Exception:
                pass  # a dead console must not stop the run

    def skip(self, wallet: str, serial: str, reason: str) -> None:
        wdir = self.session / wallet
        wdir.mkdir(parents=True, exist_ok=True)
        (wdir / "pytest.out").write_text(f"Skipped by the parallel runner: {reason}\n")
        self.results[wallet] = Result(wallet, serial, "skipped", reason=reason)
        self.say(f"SKIP {wallet} lane={serial}: {reason}")

    # -- probing

    def probe(self, device: Device, server: str) -> Tuple[str, str]:
        ok, detail = self.ctx.probe_device(device.serial)
        if not ok:
            return "down", f"device {device.serial} is not ready ({detail})"
        ok, detail = self.ctx.probe_server(server)
        if not ok:
            return "down", f"Appium server {server} is not ready ({detail})"
        if busy_logcats(self.ctx.list_processes(), device.serial):
            return "busy", (f"device {device.serial} is busy: a logcat capture from another "
                            f"test run is streaming from it")
        return "ready", "ready"

    def note(self, device: Device, status: str) -> None:
        if device.serial not in self.eligible:
            return
        with self.lock:
            self.state[device.serial] = "dead" if status == "down" else "alive"
            everyone_dead = all(v == "dead" for v in self.state.values())
            if not everyone_dead:
                self.all_dead_since = None
            elif self.all_dead_since is None:
                self.all_dead_since = self.ctx.clock()

    def wait_ready(self, device: Device, server: str) -> Tuple[str, str]:
        start = self.ctx.clock()
        while True:
            status, detail = self.probe(device, server)
            self.note(device, status)
            if status == "ready" or self.stop.is_set():
                return status, detail
            budget = self.ctx.busy_wait if status == "busy" else self.ctx.wait
            remaining = budget - (self.ctx.clock() - start)
            if remaining <= 0:
                return status, detail
            self.ctx.sleep(min(POLL_SECONDS, remaining))

    def give_up_floating(self) -> bool:
        with self.lock:
            return bool(self.floating and self.all_dead_since is not None
                        and self.ctx.clock() - self.all_dead_since >= self.ctx.wait)

    # -- one device

    def worker(self, device: Device) -> None:
        pinned = deque(device.pinned)
        lock = self.server_locks.get(normalize_server(device.server))
        while not self.stop.is_set():
            if not self.acquire(lock):
                return
            try:
                if pinned:
                    item = pinned.popleft()
                    status, detail = self.wait_ready(device, item.server)
                    if status == "ready":
                        self.run_one(device, item.wallet, inject=False)
                    elif not self.stop.is_set():
                        self.skip(item.wallet, device.serial, detail)
                    continue
                step = self.floating_step(device)
            finally:
                if lock is not None:
                    lock.release()
            if step == "done":
                return
            if step == "idle":
                self.ctx.sleep(POLL_SECONDS)

    def acquire(self, lock: Optional[threading.Lock]) -> bool:
        if lock is None:
            return True
        while not self.stop.is_set():
            if lock.acquire(blocking=False):
                return True
            self.ctx.sleep(TICK_SECONDS)
        return False

    def floating_step(self, device: Device) -> str:
        """Try to take one floating wallet: 'ran', 'idle' (not ready, try again) or 'done'."""
        with self.lock:
            if not self.floating or device.serial not in self.eligible:
                return "done"
        status, detail = self.probe(device, device.server)
        self.note(device, status)
        if status == "ready":
            with self.lock:
                wallet = self.floating.popleft() if self.floating else None
            if wallet is None:
                return "done"
            self.run_one(device, wallet, inject=True)
            return "ran"
        if self.give_up_floating():
            with self.lock:
                left, self.floating = list(self.floating), deque()
            for wallet in left:
                self.skip(wallet, "-", f"no device became ready within {self.ctx.wait:g}s "
                                       f"(last answer: {detail})")
            return "done"
        return "idle"

    def run_one(self, device: Device, wallet: str, inject: bool) -> None:
        """One wallet; whatever goes wrong with it must not end this device's lane."""
        try:
            self.execute(device, wallet, inject)
        except Exception as e:
            self.skip(wallet, device.serial, f"could not start: {type(e).__name__}")

    def execute(self, device: Device, wallet: str, inject: bool) -> None:
        if self.stop.is_set():
            return
        ctx = self.ctx
        wdir = self.session / wallet
        wdir.mkdir(parents=True, exist_ok=True)
        env = dict(ctx.environ)
        env.pop("PYTEST_RUN_DIR", None)
        env["PYTEST_SESSION_DIR"] = str(self.session)
        if inject:
            prefix = wallet.upper()
            env[f"{prefix}_DEVICE_NAME"] = device.name
            env[f"{prefix}_APPIUM_SERVER"] = device.server
            env[f"{prefix}_DEVICE_PIN"] = env[device.pin_var]
        argv = [ctx.python, "-m", "pytest", str(ctx.repo_root / "wallets" / wallet / "tests"),
                "-v", "-p", "no:cacheprovider"] + ctx.extras
        relative = (wdir / "pytest.out").relative_to(ctx.repo_root)
        with open(wdir / "pytest.out", "w") as sink:
            sink.write(f"Run by the parallel runner: {wallet} on {device.serial} via "
                       f"{device.server}\n")
            sink.flush()
            started = ctx.clock()
            proc = ctx.popen(argv, cwd=str(ctx.repo_root), env=env, stdout=sink,
                             stderr=subprocess.STDOUT, start_new_session=True)
        with self.lock:
            self.all_dead_since = None
            if device.serial in self.state:
                self.state[device.serial] = "alive"
        self.say(f"lane {device.serial}  START {wallet} -> {relative}")
        code, killed = self.watch(proc)
        seconds = ctx.clock() - started
        state = "killed" if killed else "done"
        self.results[wallet] = Result(wallet, device.serial, state, code, seconds,
                                      "killed by the runner" if killed else "")
        self.say(f"lane {device.serial}  {state.upper()} {wallet} exit {code} "
                 f"{fmt_duration(seconds)}")

    def watch(self, proc) -> Tuple[int, bool]:
        """Wait for the child; returns (exit code, killed by us). On interrupt: one SIGINT to the
        child only (never its group: that would kill the adb logcat the child's own cleanup is
        still reading), SIGKILL to the group after the grace period, or at once on a second
        interrupt."""
        ctx = self.ctx
        sigint_deadline = None
        killed = False

        def send(fn, sig):
            try:
                fn(proc.pid, sig)
            except (ProcessLookupError, PermissionError):
                pass  # already gone; macOS answers EPERM for a group holding only a zombie

        while proc.poll() is None:
            if self.force.is_set() and not killed:
                send(ctx.killpg, signal.SIGKILL)
                killed = True
            elif self.stop.is_set() and sigint_deadline is None:
                send(ctx.kill, signal.SIGINT)
                sigint_deadline = ctx.clock() + GRACE_SECONDS
            elif sigint_deadline is not None and not killed and ctx.clock() >= sigint_deadline:
                send(ctx.killpg, signal.SIGKILL)
                killed = True
            ctx.sleep(TICK_SECONDS)
        code = proc.poll()
        return (128 - code if code < 0 else code), killed

    # -- the whole run

    def run(self) -> List[Result]:
        self.ctx.install_signals(self.stop, self.force)  # before any child exists
        self.session = self.ctx.session_dir()
        self.say(f"Session {self.session.relative_to(self.ctx.repo_root)}")
        for wallet, why in self.plan.unresolved:
            self.skip(wallet, "-", why)
        if self.floating and not self.eligible:
            for wallet in list(self.floating):
                self.skip(wallet, "-", "no device in the pool has a known screen-lock PIN "
                                       "and a single known server")
            self.floating.clear()
        threads = []
        for device in self.plan.devices.values():
            if device.pinned or device.serial in self.eligible:
                t = threading.Thread(target=self.guarded, args=(device,), daemon=True)
                threads.append(t)
                t.start()
        self.join_all(threads)
        reason = "interrupted" if self.stop.is_set() else "no device took it"
        for wallet in self.plan.wallets:
            result = self.results[wallet]
            if result.state == "not started":
                result.reason = reason
                self.say(f"NOT STARTED {wallet} ({reason})")
        return [self.results[w] for w in self.plan.wallets]

    def join_all(self, threads) -> None:
        """Wait for the lanes. Anything that interrupts the wait counts as a stop request, so the
        children still get their one SIGINT instead of being left behind."""
        while True:
            try:
                while any(t.is_alive() for t in threads):
                    for t in threads:
                        t.join(timeout=0.2)
                return
            except BaseException:
                self.stop.set()

    def guarded(self, device: Device) -> None:
        try:
            self.worker(device)
        except Exception as e:
            self.say(f"lane {device.serial}  WORKER FAILED {type(e).__name__}: {e}")


def format_table(results: List[Result]) -> List[str]:
    rows = [("wallet", "device", "state", "exit", "duration")]
    for r in results:
        state = r.state if r.state == "done" else f"{r.state} ({r.reason})"
        rows.append((r.wallet, r.device, state,
                     "" if r.code is None else str(r.code),
                     fmt_duration(r.seconds) if r.state in ("done", "killed") else ""))
    widths = [max(len(row[i]) for row in rows) for i in range(5)]
    return ["  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)).rstrip() for row in rows]


# --- command line ---------------------------------------------------------------------------

def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run_parallel.py", allow_abbrev=False,
        description=("Run wallet test suites in parallel, one worker per device. A wallet with a "
                     "non-blank <WALLET>_DEVICE_NAME in .env is pinned to that device; every other "
                     "wallet waits in one queue and the first ready device takes it. Anything "
                     "else starting with '-' goes to pytest; put pytest arguments that take a "
                     "value after '--'."),
        epilog=("Exit code: 130 after an interrupt; else the highest pytest code other than 0, 1 "
                "and 5; else 10 if a wallet was skipped; else the highest code."))
    parser.add_argument("wallets", nargs="*", help="wallets to run (default: all but example)")
    parser.add_argument("--wait", type=float, default=DEFAULT_WAIT, metavar="SECONDS",
                        help="how long to wait for a device or Appium server to become ready "
                             "before skipping a pinned wallet (default %(default)s; 0 probes "
                             "once)")
    parser.add_argument("--busy-wait", type=float, default=DEFAULT_BUSY_WAIT, metavar="SECONDS",
                        help="how long to wait when the device is busy instead of down; Appium's "
                             "own logcat capture can outlive a session by about a minute "
                             "(default %(default)s)")
    parser.add_argument("--allow-shared-server", action="store_true",
                        help="let devices that use the same Appium server run at the same time "
                             "(their appium.log files then absorb each other's lines)")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the plan and exit; no device, server, lock or file is touched")
    return parser


def main(argv: Optional[List[str]] = None, ctx: Optional[Ctx] = None, env=None,
         load: Callable = load_config, available: Optional[List[str]] = None,
         server_default: Optional[str] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    tail = []
    if "--" in argv:
        split = argv.index("--")
        argv, tail = argv[:split], argv[split + 1:]
    args, unknown = make_parser().parse_known_args(argv)
    stray = [t for t in unknown if not t.startswith("-")]
    if stray:
        print(f"Cannot tell whether {', '.join(stray)} is a wallet or a pytest value. Put pytest "
              "arguments that take a value after '--'.")
        return 2
    extras = unknown + tail
    env = os.environ if env is None else env
    known = list_wallets() if available is None else available
    if args.wallets:
        bad = [w for w in args.wallets if w not in known]
        if bad:
            print(f"Unknown wallet(s): {', '.join(bad)}")
            print(f"Available: {', '.join(known)}")
            return 1
        wallets = list(dict.fromkeys(args.wallets))
    else:
        wallets = [w for w in known if w != "example"]
    plan = build_plan(wallets, env, load,
                      server_default if server_default is not None else default_server())
    if args.dry_run:
        for line in describe_plan(plan):
            print(line)
        return 1 if plan.unresolved else 0
    ctx = ctx or Ctx()
    ctx.wait, ctx.busy_wait = args.wait, args.busy_wait
    ctx.allow_shared_server, ctx.extras = args.allow_shared_server, extras
    scheduler = Scheduler(plan, ctx)
    results = scheduler.run()
    code = exit_code(results, scheduler.stop.is_set())
    for line in format_table(results) + [f"Exit code {code}"]:
        try:
            ctx.out(line)
        except Exception:
            break
    return code


if __name__ == "__main__":
    sys.exit(main())
