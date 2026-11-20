"""runners/run_parallel.py: planning, scheduling, interrupts and the PIN rule.

No device, socket, adb or real sleep is involved: every probe, process and clock is a fake handed
to the runner, and the threads share one fake clock.
"""
import importlib.util
import os
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import pytest

import conftest as root_conftest

_SPEC = importlib.util.spec_from_file_location(
    "run_parallel", Path(__file__).resolve().parents[2] / "runners" / "run_parallel.py")
rp = importlib.util.module_from_spec(_SPEC)
sys.modules["run_parallel"] = rp  # dataclasses looks the module up by name
_SPEC.loader.exec_module(rp)

PIN_PHONE = "PIN-SENTINEL-phone"
PIN_EMU = "PIN-SENTINEL-emulator"
PINS = (PIN_PHONE, PIN_EMU)
LOCAL = "http://127.0.0.1:4723"
OTHER = "http://127.0.0.1:4724"


def env_of(**extra):
    env = {"DEVICE_NAME": "phone", "DEVICE_PIN": PIN_PHONE}
    env.update(extra)
    return env


def fake_load(env, default=LOCAL):
    def load(wallet):
        prefix = wallet.upper()
        name = env.get(f"{prefix}_DEVICE_NAME") or env.get("DEVICE_NAME")
        if not name:
            raise pytest.UsageError("DEVICE_NAME is not set")
        return {"server": env.get(f"{prefix}_APPIUM_SERVER") or default,
                "android": {"device_name": name}}
    return load


def plan_of(env, wallets, default=LOCAL):
    return rp.build_plan(wallets, env, fake_load(env, default), default)


# --- fakes ------------------------------------------------------------------------------------

class Clock:
    def __init__(self):
        self.t = 0.0
        self.lk = threading.Lock()
        self.hook = None
        self.sleeps = {}

    def now(self):
        return self.t

    def sleep(self, dt):
        with self.lk:
            self.t += dt
            name = threading.current_thread().name
            self.sleeps[name] = self.sleeps.get(name, 0) + 1
        if self.hook:
            self.hook(self)
        time.sleep(0)  # let the other lane threads run


class FakeProc:
    def __init__(self, pid, clock, duration, code, obeys_sigint, gate=None):
        self.pid, self.clock, self.code = pid, clock, code
        self.gate, self.ended = gate, False
        self.thread = threading.current_thread().name
        self.finish_at = clock.t + duration
        self.obeys_sigint = obeys_sigint
        self.sigint_at = None
        self.killed = False

    def poll(self):
        code = self._code()
        self.ended = self.ended or code is not None
        return code

    def _code(self):
        if self.killed:
            return -9
        if self.sigint_at is not None and self.obeys_sigint and self.clock.t >= self.sigint_at + 3:
            return 2
        if self.gate and not self.gate(self):
            return None
        return self.code if self.clock.t >= self.finish_at else None


class Rig:
    def __init__(self, tmp_path, env, wait=30.0):
        self.clock = Clock()
        self.env = env
        self.spawned, self.procs, self.kills, self.killpgs, self.events = [], {}, [], [], []
        self.lines = []
        self.dur, self.codes = {}, {}
        self.down_always, self.down_until, self.down_from = set(), {}, {}
        self.server_down = set()
        self.ps = lambda t: ""
        self.probe_calls = []
        self.obeys_sigint = True
        self.gated = set()
        self.gate_need, self.gate_patience = 2, 20
        self.active_max = 0
        self.session = tmp_path / "reports" / "2026-01-01_00-00-00"
        self.ctx = rp.Ctx(
            session_dir=self.make_session, popen=self.popen, probe_device=self.probe_device,
            probe_server=self.probe_server, list_processes=lambda: self.ps(self.clock.t),
            sleep=self.clock.sleep, clock=self.clock.now,
            wall=lambda: datetime(2026, 1, 1, 12, 0, 0), out=self.lines.append,
            kill=self.kill, killpg=self.killpg, install_signals=self.install_signals,
            environ=env, repo_root=tmp_path, python="/venv/python", wait=wait)

    def make_session(self):
        self.session.mkdir(parents=True)
        return self.session

    def install_signals(self, stop, force):
        self.events.append("signals")

    def probe_device(self, serial):
        t = self.clock.t
        self.probe_calls.append((serial, t))
        down = (serial in self.down_always or t < self.down_until.get(serial, -1)
                or t >= self.down_from.get(serial, float("inf")))
        return (False, "offline") if down else (True, "device")

    def probe_server(self, url):
        return (False, "refused") if url in self.server_down else (True, "ready")

    def popen(self, argv, **kwargs):
        self.events.append("popen")
        wallet = Path(argv[3]).parent.name
        proc = FakeProc(1000 + len(self.spawned), self.clock, self.dur.get(wallet, 10),
                        self.codes.get(wallet, 0), self.obeys_sigint,
                        self.hold_open if wallet in self.gated else None)
        self.active_max = max(self.active_max,
                              1 + sum(1 for p in self.procs.values() if not p.ended))
        self.procs[proc.pid] = proc
        self.spawned.append({"wallet": wallet, "argv": argv, "kwargs": kwargs, "t": self.clock.t,
                             "end": proc.finish_at, "pid": proc.pid})
        return proc

    def hold_open(self, proc):
        """A gated child stays alive until another lane has started a child or has demonstrably
        been waiting: that makes 'did these two run at once?' independent of thread timing."""
        others = sum(n for name, n in self.clock.sleeps.items() if name != proc.thread)
        started = sum(1 for s in self.spawned if s["wallet"] in self.gated)
        return started >= self.gate_need or others >= self.gate_patience

    def kill(self, pid, sig):
        self.kills.append((self.clock.t, pid, sig))
        self.procs[pid].sigint_at = self.clock.t

    def killpg(self, pid, sig):
        self.killpgs.append((self.clock.t, pid, sig))
        self.procs[pid].killed = True

    def start_of(self, wallet):
        return next(s for s in self.spawned if s["wallet"] == wallet)

    def run(self, wallets, extras=(), allow_shared=False, pinned_env=None):
        plan = rp.build_plan(wallets, self.env, fake_load(self.env), LOCAL)
        self.ctx.extras = list(extras)
        self.ctx.allow_shared_server = allow_shared
        self.sched = rp.Scheduler(plan, self.ctx)
        self.plan = plan
        self.results = {r.wallet: r for r in self.sched.run()}
        return self.results

    def out_text(self):
        parts = list(self.lines)
        for f in self.session.rglob("pytest.out"):
            parts.append(f.read_text())
        return "\n".join(parts)


@pytest.fixture
def rig(tmp_path):
    def make(env, wait=30.0):
        return Rig(tmp_path, env, wait)
    return make


# --- target resolution --------------------------------------------------------------------------

CONFIGS = [
    {"server": LOCAL, "android": {"device_name": "emulator-5554"}},
    {"server": LOCAL, "android": {"device_name": "emulator-5554", "udid": "R58M1234"}},
    {"server": LOCAL, "android": {"devices": [{"device_name": "a", "server": OTHER},
                                              {"device_name": "b", "server": LOCAL, "udid": "u"}]}},
]


@pytest.mark.parametrize("config", CONFIGS)
def test_resolve_target_matches_the_conftest_single_process_resolution(config):
    udid, name, server = rp.resolve_target("w", load=lambda w: config)
    expected = root_conftest._resolve_device(config, "master")
    assert (udid, name, server) == (expected["udid"], expected["device_name"], expected["server"])


def test_resolve_target_takes_the_udid_override_and_the_first_device():
    assert rp.resolve_target("w", lambda w: CONFIGS[1])[0] == "R58M1234"
    assert rp.resolve_target("w", lambda w: CONFIGS[2]) == ("a", "a", OTHER)


# --- planning -------------------------------------------------------------------------------------

def test_a_wallet_with_a_device_variable_is_pinned_and_the_rest_float():
    plan = plan_of(env_of(HEIDI_DEVICE_NAME="emu", HEIDI_DEVICE_PIN=PIN_EMU), ["heidi", "hovi"])
    assert [p.wallet for p in plan.devices["emu"].pinned] == ["heidi"]
    assert plan.floating == ["hovi"]


def test_a_blank_device_variable_does_not_pin_the_wallet():
    plan = plan_of(env_of(HEIDI_DEVICE_NAME="  "), ["heidi"])
    assert plan.floating == ["heidi"] and not plan.devices["phone"].pinned


@pytest.mark.parametrize("error", [pytest.UsageError("bad"), RuntimeError("boom"), KeyError("x")])
def test_a_wallet_whose_config_fails_is_unresolved_not_fatal(error):
    env = env_of(HEIDI_DEVICE_NAME="emu", HOVI_DEVICE_NAME="emu2")

    def load(wallet):
        if wallet == "heidi":
            raise error
        return fake_load(env)(wallet)
    plan = rp.build_plan(["heidi", "hovi"], env, load, LOCAL)
    assert [w for w, _ in plan.unresolved] == ["heidi"]
    assert "emu2" in plan.devices


def test_devices_are_keyed_by_udid_and_listed_in_first_appearance_order():
    env = env_of(A_DEVICE_NAME="x", B_DEVICE_NAME="y", C_DEVICE_NAME="x")
    plan = plan_of(env, ["a", "b", "c"])
    assert list(plan.devices) == ["phone", "x", "y"]
    assert [p.wallet for p in plan.devices["x"].pinned] == ["a", "c"]

    def load(wallet):
        return {"server": LOCAL, "android": {"device_name": "n", "udid": "serial-" + wallet}}
    plan = rp.build_plan(["a", "b"], {"A_DEVICE_NAME": "n", "B_DEVICE_NAME": "n"}, load, LOCAL)
    assert list(plan.devices) == ["serial-a", "serial-b"]


def test_servers_are_compared_normalized():
    env = env_of(A_DEVICE_NAME="x", A_APPIUM_SERVER="http://localhost:4723/")
    plan = plan_of(env, ["a"])
    assert rp.shared_servers(plan) == {LOCAL: ["phone", "x"]}
    plan = plan_of(env_of(A_DEVICE_NAME="x", A_APPIUM_SERVER=OTHER), ["a"])
    assert rp.shared_servers(plan) == {}


def test_default_server_comes_from_the_device_json_expansion(monkeypatch):
    monkeypatch.delenv("DEFAULT_APPIUM_SERVER", raising=False)
    assert rp.default_server() == "http://localhost:4723"
    monkeypatch.setenv("DEFAULT_APPIUM_SERVER", "http://127.0.0.1:9999")
    assert rp.default_server() == "http://127.0.0.1:9999"


def test_pin_known_only_from_non_blank_agreeing_sources():
    plan = plan_of(env_of(), [])
    assert plan.devices["phone"].pin_var == "DEVICE_PIN"
    assert plan_of(env_of(DEVICE_PIN=""), []).devices["phone"].pin_var is None
    # a pinned wallet on another device with its own PIN
    plan = plan_of(env_of(A_DEVICE_NAME="x", A_DEVICE_PIN=PIN_EMU), ["a"])
    assert plan.devices["x"].pin_var == "A_DEVICE_PIN"
    # without its own PIN the device is unknown, even though load_config would fall back
    plan = plan_of(env_of(A_DEVICE_NAME="x"), ["a"])
    assert plan.devices["x"].pin_var is None and "_DEVICE_PIN" in plan.devices["x"].pin_note
    # one wallet with a PIN and one without
    plan = plan_of(env_of(A_DEVICE_NAME="x", A_DEVICE_PIN=PIN_EMU, B_DEVICE_NAME="x"), ["a", "b"])
    assert plan.devices["x"].pin_var is None
    # two wallets that disagree
    plan = plan_of(env_of(A_DEVICE_NAME="x", A_DEVICE_PIN=PIN_EMU, B_DEVICE_NAME="x",
                          B_DEVICE_PIN="other"), ["a", "b"])
    assert plan.devices["x"].pin_var is None and "disagree" in plan.devices["x"].pin_note
    # a pinned wallet on the default device that disagrees with DEVICE_PIN
    plan = plan_of(env_of(A_DEVICE_NAME="phone", A_DEVICE_PIN=PIN_EMU), ["a"])
    assert plan.devices["phone"].pin_var is None


def test_default_device_takes_the_server_of_its_pinned_wallets():
    env = env_of(A_DEVICE_NAME="phone", A_APPIUM_SERVER=OTHER, B_DEVICE_NAME="emu",
                 B_DEVICE_PIN="p")
    plan = plan_of(env, ["a", "b"])
    assert plan.devices["phone"].server == OTHER
    assert plan.devices["emu"].server == LOCAL
    assert rp.shared_servers(plan) == {}


def test_default_device_without_pinned_wallets_uses_the_default_server():
    plan = plan_of(env_of(B_DEVICE_NAME="emu", B_DEVICE_PIN="p"), ["b"], default="http://h:1")
    assert plan.devices["phone"].server == "http://h:1"


def test_pinned_wallets_that_disagree_on_the_server_make_it_ambiguous(rig):
    env = env_of(A_DEVICE_NAME="phone", A_APPIUM_SERVER=LOCAL,
                 B_DEVICE_NAME="phone", B_APPIUM_SERVER=OTHER)
    plan = plan_of(env, ["a", "b"])
    device = plan.devices["phone"]
    assert device.server_ambiguous and not device.takes_floating
    assert "ambiguous" in "\n".join(rp.describe_plan(plan))
    h = rig(env, wait=0)
    results = h.run(["a", "b", "f"])
    assert results["f"].state == "skipped"  # nothing else in the pool, and phone is not offered
    assert results["a"].state == results["b"].state == "done"  # each ran on its own server


def test_a_pinned_wallet_on_the_default_device_uses_device_pin():
    plan = plan_of(env_of(A_DEVICE_NAME="phone"), ["a"])
    assert plan.devices["phone"].pin_var == "DEVICE_PIN"


def test_plan_objects_never_hold_a_pin_value():
    plan = plan_of(env_of(A_DEVICE_NAME="x", A_DEVICE_PIN=PIN_EMU), ["a", "b"])
    text = repr(plan) + repr(list(plan.devices.values()))
    assert not any(pin in text for pin in PINS)


# --- command line and dry run ------------------------------------------------------------------

def boom(*a, **k):
    raise AssertionError("the dry run must not touch anything")


def test_unknown_wallet_exits_one(capsys):
    assert rp.main(["nope"], ctx=rp.Ctx(), env=env_of(), available=["hovi", "example"]) == 1
    assert "Unknown wallet(s): nope" in capsys.readouterr().out


def test_default_wallet_list_leaves_out_example(capsys):
    code = rp.main(["--dry-run"], env=env_of(), load=fake_load(env_of()),
                   available=["example", "hovi", "heidi"], server_default=LOCAL)
    out = capsys.readouterr().out
    assert code == 0 and "hovi, heidi" in out and "example" not in out


def test_dry_run_touches_nothing_and_prints_no_pin(monkeypatch, capsys):
    env = env_of(HEIDI_DEVICE_NAME="emu", HEIDI_DEVICE_PIN=PIN_EMU, TOPPAN_DEVICE_NAME="gone",
                 GATACA_DEVICE_NAME="emu2")
    monkeypatch.setattr(subprocess, "Popen", boom)
    monkeypatch.setattr(subprocess, "run", boom)
    monkeypatch.setattr(rp, "make_session_dir", boom)
    monkeypatch.setattr(Path, "mkdir", boom)
    ctx = rp.Ctx(popen=boom, probe_device=boom, probe_server=boom, list_processes=boom,
                 session_dir=boom, install_signals=boom)

    def load(wallet):
        if wallet == "toppan":
            raise RuntimeError("no such device")
        return fake_load(env)(wallet)
    code = rp.main(["--dry-run", "heidi", "hovi", "toppan", "gataca"], ctx=ctx, env=env,
                   load=load, available=["heidi", "hovi", "toppan", "gataca"],
                   server_default=LOCAL)
    out = capsys.readouterr().out
    assert code == 1  # toppan is unresolved
    assert "emu: server" in out and "PIN known: yes" in out
    assert "emu2" in out and "PIN known: no" in out and "takes no floating wallets" in out
    assert "Pinned wallets" in out and "heidi" in out
    assert "Floating queue" in out and "hovi" in out and "toppan: its configuration" in out
    assert "shares the server" in out
    assert not any(pin in out for pin in PINS)


def test_extra_arguments_reach_pytest_before_and_after_the_separator(rig):
    h = rig(env_of())
    code = rp.main(["hovi", "-x", "--", "-k", "foo bar"], ctx=h.ctx, env=h.env,
                   load=fake_load(h.env), available=["hovi"], server_default=LOCAL)
    assert code == 0
    assert h.spawned[0]["argv"][-3:] == ["-x", "-k", "foo bar"]


# --- running a child -----------------------------------------------------------------------------

def test_child_command_environment_and_output(rig, monkeypatch, tmp_path):
    env = env_of(PYTEST_RUN_DIR="stale", PYTEST_SESSION_DIR="relative/stale")
    h = rig(env)
    before_env, before_os = dict(env), dict(os.environ)
    h.run(["hovi"], extras=["-x"])
    spawn = h.start_of("hovi")
    assert spawn["argv"] == ["/venv/python", "-m", "pytest",
                             str(tmp_path / "wallets" / "hovi" / "tests"), "-v", "-p",
                             "no:cacheprovider", "-x"]
    assert "-n" not in spawn["argv"] and "no:logging" not in spawn["argv"]
    kw = spawn["kwargs"]
    assert kw["cwd"] == str(tmp_path) and kw["start_new_session"] is True
    assert kw["stderr"] == subprocess.STDOUT
    assert Path(kw["stdout"].name) == h.session / "hovi" / "pytest.out"
    assert "PYTEST_RUN_DIR" not in kw["env"]
    assert kw["env"]["PYTEST_SESSION_DIR"] == str(h.session) and h.session.is_absolute()
    assert env == before_env and dict(os.environ) == before_os  # the runner's own environment


def test_a_pinned_wallet_gets_no_injected_keys(rig):
    env = env_of(HEIDI_DEVICE_NAME="emu", HEIDI_DEVICE_PIN=PIN_EMU)
    h = rig(env)
    h.run(["heidi"])
    child = h.start_of("heidi")["kwargs"]["env"]
    assert child == {**env, "PYTEST_SESSION_DIR": str(h.session)}


def test_a_floating_wallet_gets_the_three_keys_for_its_device_only_in_its_child(rig):
    env = env_of(HEIDI_DEVICE_NAME="emu", HEIDI_DEVICE_PIN=PIN_EMU, HEIDI_APPIUM_SERVER=OTHER)
    h = rig(env)
    h.down_always.add("phone")
    results = h.run(["heidi", "hovi"])
    child = h.start_of("hovi")["kwargs"]["env"]
    assert results["hovi"].device == "emu"
    assert child["HOVI_DEVICE_NAME"] == "emu"
    assert child["HOVI_APPIUM_SERVER"] == OTHER
    assert child["HOVI_DEVICE_PIN"] == PIN_EMU
    assert "HOVI_DEVICE_NAME" not in env  # the runner's mapping is untouched


# --- the PIN never leaves the child --------------------------------------------------------------

def test_no_pin_appears_in_console_files_or_results(rig):
    env = env_of(HEIDI_DEVICE_NAME="emu", HEIDI_DEVICE_PIN=PIN_EMU,
                 TOPPAN_DEVICE_NAME="gone", TOPPAN_DEVICE_PIN=PIN_EMU)
    h = rig(env, wait=6)
    h.down_always.add("gone")
    results = h.run(["heidi", "toppan", "hovi", "paradym"])
    assert results["toppan"].state == "skipped" and results["hovi"].state == "done"
    injected = h.start_of("hovi")["kwargs"]["env"]
    assert PIN_PHONE in injected.values() or PIN_EMU in injected.values()  # the test is live
    text = h.out_text() + repr(results) + repr(h.plan) + "\n".join(rp.format_table(
        list(results.values())))
    assert not any(pin in text for pin in PINS)


# --- skips -------------------------------------------------------------------------------------

def test_a_pinned_wallet_on_a_dead_device_is_skipped_with_a_reason_file(rig):
    env = env_of(HEIDI_DEVICE_NAME="emu", HEIDI_DEVICE_PIN=PIN_EMU)
    h = rig(env, wait=12)
    h.down_always.add("emu")
    results = h.run(["heidi", "hovi"])
    assert results["heidi"].state == "skipped"
    assert "emu" in (h.session / "heidi" / "pytest.out").read_text()
    assert results["hovi"].state == "done" and results["hovi"].device == "phone"
    assert rp.exit_code(list(results.values()), False) == 10


def test_a_server_that_is_down_skips_the_pinned_wallet(rig):
    env = env_of(HEIDI_DEVICE_NAME="emu", HEIDI_DEVICE_PIN=PIN_EMU, HEIDI_APPIUM_SERVER=OTHER)
    h = rig(env, wait=0)
    h.server_down.add(OTHER)
    results = h.run(["heidi"])
    assert results["heidi"].state == "skipped" and OTHER in results["heidi"].reason


def test_a_dead_device_skips_all_its_pinned_wallets_but_the_other_lane_continues(rig):
    env = env_of(A_DEVICE_NAME="x", A_DEVICE_PIN="p", B_DEVICE_NAME="x", B_DEVICE_PIN="p",
                 C_DEVICE_NAME="y", C_DEVICE_PIN="p")
    h = rig(env, wait=0)
    h.down_always.add("x")
    results = h.run(["a", "b", "c"])
    assert [results[w].state for w in "abc"] == ["skipped", "skipped", "done"]


def test_a_device_that_returns_within_the_wait_runs_the_wallet(rig):
    env = env_of(HEIDI_DEVICE_NAME="emu", HEIDI_DEVICE_PIN=PIN_EMU)
    h = rig(env, wait=30)
    h.down_until["emu"] = 12
    results = h.run(["heidi"])
    assert results["heidi"].state == "done"
    assert h.start_of("heidi")["t"] >= 12


def test_each_pinned_wallet_is_probed_against_its_own_server(rig):
    env = env_of(A_DEVICE_NAME="x", A_DEVICE_PIN="p", A_APPIUM_SERVER=LOCAL,
                 B_DEVICE_NAME="x", B_APPIUM_SERVER=OTHER)
    h = rig(env, wait=0)
    h.server_down.add(OTHER)
    results = h.run(["a", "b"])
    assert results["a"].state == "done"
    assert results["b"].state == "skipped" and OTHER in results["b"].reason


def test_an_interrupt_during_the_wait_starts_nothing(rig):
    env = env_of(A_DEVICE_NAME="x", A_DEVICE_PIN="p")
    h = rig(env)
    plan = rp.build_plan(["a"], h.env, fake_load(h.env), LOCAL)
    sched = rp.Scheduler(plan, h.ctx)

    def probe_then_interrupt(serial):
        sched.stop.set()  # Ctrl-C lands after the device answered and before the child starts
        return True, "device"
    h.ctx.probe_device = probe_then_interrupt
    results = {r.wallet: r for r in sched.run()}
    assert h.spawned == [] and results["a"].state == "not started"


def test_a_busy_device_makes_a_pinned_wallet_wait_then_skip(rig):
    env = env_of(HEIDI_DEVICE_NAME="emu", HEIDI_DEVICE_PIN=PIN_EMU)
    h = rig(env, wait=10)
    h.ps = lambda t: " 500 400 adb -s emu logcat -v threadtime\n"
    results = h.run(["heidi"])
    assert results["heidi"].state == "skipped" and "busy" in results["heidi"].reason


def test_wait_budget_is_measured_on_the_clock(rig):
    env = env_of(HEIDI_DEVICE_NAME="emu", HEIDI_DEVICE_PIN=PIN_EMU)
    h = rig(env, wait=12)
    h.down_always.add("emu")
    h.run(["heidi"])
    assert [t for s, t in h.probe_calls if s == "emu"] == [0, 5, 10, 12]


def test_wait_zero_probes_once(rig):
    env = env_of(HEIDI_DEVICE_NAME="emu", HEIDI_DEVICE_PIN=PIN_EMU)
    h = rig(env, wait=0)
    h.down_always.add("emu")
    h.run(["heidi"])
    assert len([c for c in h.probe_calls if c[0] == "emu"]) == 1


def test_the_device_is_probed_again_before_every_wallet(rig):
    env = env_of(A_DEVICE_NAME="x", A_DEVICE_PIN="p", B_DEVICE_NAME="x")
    h = rig(env, wait=0)
    h.dur["a"] = 10
    h.down_from["x"] = 5  # alive for the first wallet, gone before the second
    results = h.run(["a", "b"])
    assert [results[w].state for w in "ab"] == ["done", "skipped"]


# --- floating wallets ---------------------------------------------------------------------------

def test_floating_wallets_spread_over_the_ready_devices(rig):
    env = env_of(HEIDI_DEVICE_NAME="emu", HEIDI_DEVICE_PIN=PIN_EMU, HEIDI_APPIUM_SERVER=OTHER)
    h = rig(env)
    h.dur, h.gated = {"heidi": 1, "a": 100, "b": 100}, {"a", "b"}
    results = h.run(["heidi", "a", "b"])
    assert {results["a"].device, results["b"].device} == {"phone", "emu"}
    assert all(r.state == "done" for r in results.values())


def test_a_down_device_takes_nothing_and_nothing_floating_is_skipped(rig):
    env = env_of(HEIDI_DEVICE_NAME="emu", HEIDI_DEVICE_PIN=PIN_EMU, HEIDI_APPIUM_SERVER=OTHER)
    h = rig(env, wait=30)
    h.down_always.add("phone")
    results = h.run(["heidi", "a", "b", "c"])
    assert {w: results[w].device for w in "abc"} == {"a": "emu", "b": "emu", "c": "emu"}
    assert all(r.state == "done" for r in results.values())


def test_a_device_with_an_unknown_pin_gets_no_floating_wallet(rig):
    env = env_of(A_DEVICE_NAME="x", B_DEVICE_NAME="x", B_DEVICE_PIN="p")  # a has no PIN of its own
    h = rig(env, wait=30)
    h.down_until["phone"] = 20  # x is up the whole time and must still not take f
    results = h.run(["a", "b", "f"])
    assert results["f"].device == "phone" and h.start_of("f")["t"] >= 20


def test_floating_wallets_are_skipped_when_no_device_has_a_known_pin(rig):
    h = rig(env_of(DEVICE_PIN=""))
    results = h.run(["f"])
    assert results["f"].state == "skipped" and "PIN" in results["f"].reason
    assert h.spawned == []


def test_floating_wallets_wait_while_every_device_is_down_then_give_up(rig):
    h = rig(env_of(), wait=30)
    h.down_always.add("phone")
    results = h.run(["f"])
    assert results["f"].state == "skipped" and "30s" in results["f"].reason
    assert h.clock.t >= 30


def test_floating_wallet_waits_for_a_device_that_comes_back(rig):
    h = rig(env_of(), wait=30)
    h.down_until["phone"] = 20
    assert h.run(["f"])["f"].state == "done"


def test_a_busy_device_counts_as_alive_so_floating_wallets_keep_waiting(rig):
    env = env_of(HEIDI_DEVICE_NAME="emu", HEIDI_DEVICE_PIN=PIN_EMU, HEIDI_APPIUM_SERVER=OTHER)
    h = rig(env, wait=30)
    h.down_always.add("emu")
    h.ps = lambda t: " 500 400 adb -s phone logcat\n" if t < 40 else ""
    results = h.run(["f"])
    assert results["f"].state == "done" and results["f"].device == "phone"


def test_a_device_that_is_running_a_wallet_counts_as_alive(rig):
    env = env_of(A_DEVICE_NAME="x", A_DEVICE_PIN="p", A_APPIUM_SERVER=OTHER)
    h = rig(env, wait=30)
    h.down_always.add("phone")
    h.dur["a"] = 200
    results = h.run(["a", "f"])
    assert results["f"].state == "done" and results["f"].device == "x"


# --- concurrency -----------------------------------------------------------------------------

def three_lanes():
    return env_of(A_DEVICE_NAME="x", A_DEVICE_PIN="p", A_APPIUM_SERVER="http://127.0.0.1:4801",
                  B_DEVICE_NAME="y", B_DEVICE_PIN="p", B_APPIUM_SERVER="http://127.0.0.1:4802",
                  C_DEVICE_NAME="z", C_DEVICE_PIN="p", C_APPIUM_SERVER="http://127.0.0.1:4803")


def test_lanes_on_different_servers_overlap(rig):
    h = rig(three_lanes())
    h.dur, h.gated = {"a": 100, "b": 100, "c": 100}, {"a", "b", "c"}
    h.gate_need, h.gate_patience = 3, 100000
    h.run(["a", "b", "c"])
    assert h.active_max == 3


def test_wallets_in_one_lane_run_one_after_the_other_in_list_order(rig):
    env = env_of(A_DEVICE_NAME="x", A_DEVICE_PIN="p", B_DEVICE_NAME="x", C_DEVICE_NAME="x")
    env.update(D_DEVICE_NAME="y", D_DEVICE_PIN="p", D_APPIUM_SERVER=OTHER)
    h = rig(env)
    h.dur, h.gated = {"a": 20, "b": 20, "c": 20, "d": 3000}, {"a", "b", "c"}
    h.run(["a", "b", "c", "d"])
    a, b, c = (h.start_of(w) for w in "abc")
    assert a["end"] <= b["t"] and b["end"] <= c["t"] and h.active_max == 2


def shared_server_env():
    return env_of(A_DEVICE_NAME="x", A_DEVICE_PIN="p", B_DEVICE_NAME="y", B_DEVICE_PIN="p",
                  A_APPIUM_SERVER=LOCAL, B_APPIUM_SERVER="http://localhost:4723/")


def test_devices_on_one_server_take_turns(rig):
    h = rig(shared_server_env())
    h.dur, h.gated = {"a": 50, "b": 50}, {"a", "b"}
    results = h.run(["a", "b"])
    assert h.active_max == 1
    assert all(r.state == "done" for r in results.values())


def test_allow_shared_server_lets_them_overlap(rig):
    h = rig(shared_server_env())
    h.dur, h.gated = {"a": 50, "b": 50}, {"a", "b"}
    h.run(["a", "b"], allow_shared=True)
    assert h.active_max == 2


def test_the_server_lock_is_taken_before_a_wallet_leaves_the_queue(rig):
    h = rig(env_of())
    plan = rp.build_plan(["f1", "f2"], h.env, fake_load(h.env), LOCAL)
    sched = rp.Scheduler(plan, h.ctx)
    sched.session = h.make_session()
    lock = sched.server_locks[LOCAL]
    lock.acquire()
    seen = []

    def hook(clock):
        seen.append(len(sched.floating))
        if len(seen) == 3:
            lock.release()
    h.clock.hook = hook
    sched.worker(plan.devices["phone"])
    assert seen[:3] == [2, 2, 2]  # nothing was dequeued while the lock was held
    assert sched.results["f1"].state == "done" and sched.results["f2"].state == "done"


# --- interrupts ---------------------------------------------------------------------------------

def run_interrupted(h, wallets, children=1):
    """Press Ctrl-C once `children` children are running."""
    plan = rp.build_plan(wallets, h.env, fake_load(h.env), LOCAL)
    sched = rp.Scheduler(plan, h.ctx)
    h.sched = sched
    fired = []

    def hook(clock):
        if len(h.spawned) >= children and not fired:
            fired.append(1)
            sched.stop.set()
    h.clock.hook = hook
    results = {r.wallet: r for r in sched.run()}
    return sched, results


def test_interrupt_sends_one_sigint_to_each_live_child_and_starts_nothing_more(rig):
    env = env_of(A_DEVICE_NAME="x", A_DEVICE_PIN="p", B_DEVICE_NAME="y", B_DEVICE_PIN="p",
                 C_DEVICE_NAME="x", B_APPIUM_SERVER=OTHER)
    h = rig(env)
    h.dur = {"a": 1000, "b": 1000, "c": 10}
    sched, results = run_interrupted(h, ["a", "b", "c"], children=2)
    assert sorted((pid, sig) for _, pid, sig in h.kills) == sorted(
        (s["pid"], signal.SIGINT) for s in h.spawned)
    assert len(h.kills) == 2 and h.killpgs == []
    assert results["c"].state == "not started" and results["c"].reason == "interrupted"
    assert rp.exit_code(list(results.values()), sched.stop.is_set()) == 130


def test_a_child_that_ignores_sigint_is_killed_only_after_the_grace_period(rig):
    h = rig(env_of(A_DEVICE_NAME="x", A_DEVICE_PIN="p"))
    h.obeys_sigint = False
    h.dur["a"] = 10000
    run_interrupted(h, ["a"])
    (sigint_t, pid, _), = h.kills
    (kill_t, kill_pid, sig), = h.killpgs
    assert kill_pid == pid and sig == signal.SIGKILL
    assert kill_t - sigint_t >= rp.GRACE_SECONDS


def test_a_second_interrupt_kills_the_group_at_once(rig):
    h = rig(env_of(A_DEVICE_NAME="x", A_DEVICE_PIN="p"))
    h.obeys_sigint = False
    h.dur["a"] = 10000
    plan = rp.build_plan(["a"], h.env, fake_load(h.env), LOCAL)
    sched = rp.Scheduler(plan, h.ctx)
    fired = {}

    def hook(clock):
        if h.spawned and "stop" not in fired:
            fired["stop"] = clock.t
            sched.stop.set()
        if "stop" in fired and clock.t >= fired["stop"] + 3 and "force" not in fired:
            fired["force"] = clock.t
            sched.force.set()
    h.clock.hook = hook
    sched.run()
    (kill_t, _, sig), = h.killpgs
    assert sig == signal.SIGKILL and kill_t - fired["force"] <= 2 < rp.GRACE_SECONDS


def test_signal_handlers_are_installed_before_the_first_child(rig):
    h = rig(env_of())
    h.run(["hovi", "heidi"])
    assert h.events[0] == "signals" and "popen" in h.events


def test_the_real_handler_sets_stop_then_force(monkeypatch):
    installed = {}
    monkeypatch.setattr(signal, "signal", lambda sig, fn: installed.__setitem__(sig, fn))
    stop, force = threading.Event(), threading.Event()
    rp.install_signals(stop, force)
    assert set(installed) == {signal.SIGINT, signal.SIGTERM, signal.SIGHUP}
    installed[signal.SIGINT](signal.SIGINT, None)
    assert stop.is_set() and not force.is_set()
    installed[signal.SIGINT](signal.SIGINT, None)
    assert force.is_set()


# --- exit codes -----------------------------------------------------------------------------------

def done(code):
    return rp.Result("w", "d", "done", code)


SKIPPED = rp.Result("s", "-", "skipped", reason="x")


@pytest.mark.parametrize("results,interrupted,expected", [
    ([done(0), done(0)], False, 0),
    ([done(0), done(1)], False, 1),
    ([done(1), done(5)], False, 5),
    ([done(5), done(1)], False, 5),
    ([done(1), SKIPPED], False, 10),
    ([done(0), SKIPPED, done(2)], False, 2),
    ([done(3), done(2)], False, 3),
    ([done(137), done(1)], False, 137),
    ([done(1), SKIPPED], True, 130),
    ([], False, 0),
])
def test_exit_code_precedence(results, interrupted, expected):
    assert rp.exit_code(results, interrupted) == expected


def test_a_killed_child_reports_the_shell_style_code(rig):
    h = rig(env_of(A_DEVICE_NAME="x", A_DEVICE_PIN="p"))
    h.obeys_sigint = False
    h.dur["a"] = 10000
    sched, results = run_interrupted(h, ["a"])
    assert results["a"].code == 137 and results["a"].state == "killed"
    assert "killed" in "\n".join(rp.format_table(list(results.values())))


def test_the_last_finished_code_does_not_win_over_the_highest(rig):
    env = env_of(A_DEVICE_NAME="x", A_DEVICE_PIN="p", B_DEVICE_NAME="y", B_DEVICE_PIN="p",
                 B_APPIUM_SERVER=OTHER)
    h = rig(env)
    h.dur, h.codes = {"a": 10, "b": 100}, {"a": 2, "b": 0}
    results = h.run(["a", "b"])
    assert rp.exit_code(list(results.values()), False) == 2


# --- helpers ---------------------------------------------------------------------------------------

def test_busy_logcats_counts_live_captures_only():
    ps = ("  1     0 /sbin/launchd\n"
          " 10     5 adb -s emu logcat -v threadtime\n"
          " 11     1 adb -s emu logcat -v threadtime\n"
          " 12     5 adb -s other logcat\n"
          " 13     5 adb -L tcp:5037 fork-server server\n"
          " 14     5 adb logcat\n")
    assert rp.busy_logcats(ps, "emu") == [10]
    assert rp.busy_logcats(ps, "other") == [12]


def test_session_dir_moves_to_the_next_second_when_taken(tmp_path):
    wall = lambda: datetime(2026, 1, 1, 12, 0, 0)  # noqa: E731
    first = rp.make_session_dir(wall, tmp_path)
    second = rp.make_session_dir(wall, tmp_path)
    assert first.name == "2026-01-01_12-00-00" and second.name == "2026-01-01_12-00-01"


def test_default_probes_read_adb_state_and_the_server_status(monkeypatch):
    answers = {"get-state": "device\n", "shell": "1\n"}

    class Out:
        returncode, stderr = 0, ""

        def __init__(self, stdout):
            self.stdout = stdout
    monkeypatch.setattr(subprocess, "run", lambda argv, **k: Out(answers[argv[3]]))
    assert rp.probe_device_adb("x") == (True, "device")
    answers["shell"] = "0\n"  # connected but still booting
    assert rp.probe_device_adb("x") == (False, "still booting")
    answers.update({"get-state": "offline\n", "shell": "1\n"})
    assert rp.probe_device_adb("x")[0] is False

    class Resp:
        def __init__(self, body):
            self.body = body

        def read(self):
            return self.body

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    monkeypatch.setattr(rp.urllib.request, "urlopen",
                        lambda url, timeout: Resp(b'{"value": {"ready": false}}'))
    assert rp.probe_server_http("http://x")[0] is False
    monkeypatch.setattr(rp.urllib.request, "urlopen",
                        lambda url, timeout: Resp(b'{"value": {"ready": true}}'))
    assert rp.probe_server_http("http://x")[0] is True
    for odd in (b"[]", b'{"value": "x"}', b'{"value": null}', b"null", b""):
        monkeypatch.setattr(rp.urllib.request, "urlopen", lambda url, timeout, b=odd: Resp(b))
        assert rp.probe_server_http("http://x")[0] is True  # no ready=false, so no objection
    monkeypatch.setattr(rp.urllib.request, "urlopen", lambda url, timeout: Resp(b"{not json"))
    assert rp.probe_server_http("http://x")[0] is False


def test_the_final_table_names_the_device_each_wallet_ran_on(rig):
    h = rig(env_of(A_DEVICE_NAME="x", A_DEVICE_PIN="p"))
    results = h.run(["a", "f"])
    table = "\n".join(rp.format_table(list(results.values())))
    assert "x" in table.splitlines()[1] and "phone" in table.splitlines()[2]


# --- supervision survives the console, signals and per-wallet failures ------------------------

def test_a_dead_console_does_not_stop_the_children_getting_their_interrupt(rig):
    h = rig(env_of(A_DEVICE_NAME="x", A_DEVICE_PIN="p"))
    h.dur["a"] = 1000
    broken = []

    def out(line):
        if broken or "START" in line:
            broken.append(1)
            raise BrokenPipeError("closed")
        h.lines.append(line)
    h.ctx.out = out
    sched, results = run_interrupted(h, ["a"])
    assert len(h.kills) == 1 and h.kills[0][2] == signal.SIGINT
    assert results["a"].state == "done"


def test_the_signal_handler_survives_a_closed_console(monkeypatch):
    installed = {}
    monkeypatch.setattr(signal, "signal", lambda sig, fn: installed.__setitem__(sig, fn))
    stop, force = threading.Event(), threading.Event()
    rp.install_signals(stop, force)

    def broken(*a, **k):
        raise BrokenPipeError("closed")
    monkeypatch.setattr("builtins.print", broken)
    installed[signal.SIGINT](signal.SIGINT, None)
    installed[signal.SIGINT](signal.SIGINT, None)
    assert stop.is_set() and force.is_set()
    rp.Ctx().out("still no exception")


def test_sighup_is_left_alone_when_the_runner_was_started_with_nohup(monkeypatch):
    installed = {}
    monkeypatch.setattr(signal, "signal", lambda sig, fn: installed.__setitem__(sig, fn))
    monkeypatch.setattr(signal, "getsignal", lambda sig: signal.SIG_IGN)
    rp.install_signals(threading.Event(), threading.Event())
    assert signal.SIGHUP not in installed and signal.SIGINT in installed


def test_an_interrupted_wait_counts_as_a_stop_request(rig):
    h = rig(env_of())
    sched = rp.Scheduler(plan_of(h.env, []), h.ctx)

    class Lane:
        calls = 0

        def is_alive(self):
            return self.calls < 2

        def join(self, timeout):
            self.calls += 1
            if self.calls == 1:
                raise KeyboardInterrupt
    sched.join_all([Lane()])
    assert sched.stop.is_set()


def test_kill_errors_for_a_vanished_child_are_ignored(rig):
    h = rig(env_of(A_DEVICE_NAME="x", A_DEVICE_PIN="p"))
    h.obeys_sigint = False
    h.dur["a"] = 10000
    real_kill, real_killpg = h.kill, h.killpg

    def kill(pid, sig):
        real_kill(pid, sig)
        raise ProcessLookupError

    def killpg(pid, sig):
        real_killpg(pid, sig)
        raise PermissionError
    h.ctx.kill, h.ctx.killpg = kill, killpg
    sched, results = run_interrupted(h, ["a"])
    assert results["a"].state == "killed"


def test_a_wallet_that_cannot_start_is_skipped_and_the_lane_goes_on(rig):
    h = rig(env_of(A_DEVICE_NAME="x", A_DEVICE_PIN="p", B_DEVICE_NAME="x"))
    real = h.popen

    def popen(argv, **kw):
        if "/a/" in " ".join(argv):
            raise OSError("no such interpreter")
        return real(argv, **kw)
    h.ctx.popen = popen
    results = h.run(["a", "b"])
    assert results["a"].state == "skipped" and "OSError" in results["a"].reason
    assert results["b"].state == "done"
    assert rp.exit_code(list(results.values()), False) == 10


def test_a_wallet_nobody_started_without_an_interrupt_exits_ten():
    assert rp.exit_code([done(0), rp.Result("w")], False) == 10


def test_a_running_wallet_on_a_pin_unknown_device_does_not_keep_floating_alive(rig):
    # x has no PIN of its own, so it is not in the pool for floating wallets
    env = env_of(A_DEVICE_NAME="x", DEVICE_PIN=PIN_PHONE)
    h = rig(env, wait=30)
    h.down_always.add("phone")
    h.dur["a"] = 500
    seen = {}

    def out(line):
        h.lines.append(line)
        if "SKIP f" in line:
            seen["t"] = h.clock.t
    h.ctx.out = out
    results = h.run(["a", "f"])
    assert results["f"].state == "skipped" and results["a"].state == "done"
    assert seen["t"] < h.start_of("a")["end"]  # given up while a was still running


def test_busy_devices_get_the_longer_busy_wait(rig):
    env = env_of(HEIDI_DEVICE_NAME="emu", HEIDI_DEVICE_PIN=PIN_EMU)
    h = rig(env, wait=5)
    h.ctx.busy_wait = 60
    h.ps = lambda t: " 500 400 adb -s emu logcat\n" if t < 40 else ""
    assert h.run(["heidi"])["heidi"].state == "done"


def test_the_busy_wait_is_a_limit_too(rig):
    env = env_of(HEIDI_DEVICE_NAME="emu", HEIDI_DEVICE_PIN=PIN_EMU)
    h = rig(env, wait=5)
    h.ctx.busy_wait = 20
    h.ps = lambda t: " 500 400 adb -s emu logcat\n" if t < 40 else ""
    assert h.run(["heidi"])["heidi"].state == "skipped"


def test_a_down_device_does_not_get_the_busy_wait(rig):
    env = env_of(HEIDI_DEVICE_NAME="emu", HEIDI_DEVICE_PIN=PIN_EMU)
    h = rig(env, wait=5)
    h.ctx.busy_wait = 600
    h.down_always.add("emu")
    h.run(["heidi"])
    assert h.clock.t < 100


def test_pytest_arguments_that_take_a_value_need_the_separator(capsys):
    code = rp.main(["hovi", "-k", "foo"], env=env_of(), load=fake_load(env_of()),
                   available=["hovi"], server_default=LOCAL)
    assert code == 2 and "after '--'" in capsys.readouterr().out


def test_repeated_wallet_names_run_once(capsys):
    rp.main(["hovi", "hovi", "--dry-run"], env=env_of(), load=fake_load(env_of()),
            available=["hovi"], server_default=LOCAL)
    assert "hovi, hovi" not in capsys.readouterr().out


# --- real processes ----------------------------------------------------------------------------

def test_real_process_gets_sigint_then_sigkill_to_its_group(rig, monkeypatch):
    h = rig(env_of(A_DEVICE_NAME="x", A_DEVICE_PIN="p"))
    child = ("import signal, time\nsignal.signal(signal.SIGINT, signal.SIG_IGN)\n"
             "print('up', flush=True)\ntime.sleep(30)\n")
    h.ctx.popen = lambda argv, **kw: subprocess.Popen([sys.executable, "-c", child], **kw)
    h.ctx.kill, h.ctx.killpg = os.kill, os.killpg
    h.ctx.sleep, h.ctx.clock = lambda dt: time.sleep(0.02), time.monotonic
    monkeypatch.setattr(rp, "GRACE_SECONDS", 0.3)
    plan = plan_of(h.env, ["a"])
    sched = rp.Scheduler(plan, h.ctx)
    threading.Timer(0.8, sched.stop.set).start()
    began = time.monotonic()
    results = {r.wallet: r for r in sched.run()}
    assert time.monotonic() - began < 2.5
    assert results["a"].state == "killed" and results["a"].code == 137
