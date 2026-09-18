import base64
import json
import logging
import os
import re
import subprocess
import tempfile
import time
import pytest
from datetime import datetime
from pathlib import Path

# First, and before anything that reads the environment: importing this loads .env.
from base.config import load_config, provider_matrix

from appium import webdriver
from appium.options.android.uiautomator2.base import UiAutomator2Options
from selenium.webdriver.support.ui import WebDriverWait

from base.android import (handle_biometric_if_present, handle_permission_if_present,
                          unlock_if_locked)
from base.base_test import BaseTest, UpdateNotFinished
from base.conftest_helpers import (
    capture_appium_logs,
    node_failed,
    save_failure_artifacts,
    screen_is_protected,
)
from base.utils import (list_wallets, TIMESTAMP_FORMAT, get_app_info, check_provider_reachable,
                        sanitize_test_name, device_locale)

logger = logging.getLogger(__name__)

_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_ENV_RUN_DIR = "PYTEST_RUN_DIR"
_ENV_SESSION_DIR = "PYTEST_SESSION_DIR"

_KEYCODE_WAKEUP = 224


def device_pin_for(config: dict) -> str:
    """The device's screen-lock PIN from the merged config, or "" when not configured."""
    return config.get("android", {}).get("device_pin", "")


def _is_anr_present(driver) -> bool:
    """Return True if an ANR (App Not Responding) system dialog is showing."""
    try:
        return bool(driver.find_elements(
            "xpath",
            '//*[@resource-id="android:id/aerr_wait" or @resource-id="android:id/aerr_close"]',
        ))
    except Exception:
        return False


def _clear_app_cache(driver, package: str) -> None:
    """Clear all app data and cache via ADB shell."""
    try:
        driver.execute_script("mobile: shell", {"command": "pm", "args": ["clear", package]})
        logger.info(f"[app] Cleared cache for {package}")
    except Exception as e:
        logger.warning(f"[app] Could not clear cache for {package}: {e}")


def _clear_recent_apps(driver) -> None:
    """Close the foreground app the way a user would, then return to the home screen.

    Opens recents and dismisses the app: taps 'Clear all' if the launcher has it, otherwise swipes
    the centered recents card away (the AOSP/emulator launcher has no 'Clear all' button). We
    deliberately avoid force-stop — that's a brute kill, not a user-style close.

    NOTE: closing the app is a cold start, so a wallet's transient state resets — e.g. Gataca
    reverts its active DID to the default. Flows that need a specific DID must re-establish it each
    test (the gataca conftest re-runs ensure_did per test for exactly this reason).
    """
    try:
        driver.press_keycode(187)  # KEYCODE_APP_SWITCH (recents)
        time.sleep(1)
        elems = driver.find_elements(
            "xpath",
            '//*[@text="Clear all" or @text="CLEAR ALL" or @content-desc="Clear all" or @content-desc="CLEAR ALL"]',
        )
        if elems:
            elems[0].click()
        else:
            # No 'Clear all' button: swipe the centered foreground card up to dismiss it.
            size = driver.get_window_size()
            x = size["width"] // 2
            driver.execute_script("mobile: shell", {"command": "input", "args": [
                "swipe", str(x), str(int(size["height"] * 0.6)), str(x), str(int(size["height"] * 0.1)), "250",
            ]})
        time.sleep(0.5)
    except Exception:
        pass
    try:
        driver.press_keycode(3)  # KEYCODE_HOME
    except Exception:
        pass


def _detect_wallet_name(config) -> str:
    """Return the wallet name for the current pytest run.

    Checks the collected test paths against the known wallet directories.
    Returns 'unknown' if no wallet directory matches the test path.
    """
    known = set(list_wallets())
    for arg in config.args:
        parts = Path(arg).parts
        if "wallets" in parts:
            # A bare "wallets/" has nothing after it. Guard the index rather than assuming:
            # without this, collecting the whole suite dies here with an IndexError.
            index = parts.index("wallets") + 1
            if index < len(parts) and parts[index] in known:
                return parts[index]
    return "unknown"


def pytest_configure(config):
    """Create a per-wallet run directory and wire up file logging + HTML report.

    Output layout:
        reports/<timestamp>/<wallet_name>/
    """
    config.addinivalue_line(
        "markers",
        "skip_home_setup: skip the automatic home-screen setup for this test",
    )

    # When this wallet's session began, in ms since epoch — the floor for `capture_appium_logs`.
    # The Appium server is started by hand and outlives the suite, so without a floor the first
    # test of a run inherits whatever is still in the server's buffer from previous runs.
    config._session_started_at = int(time.time() * 1000)

    if hasattr(config, "workerinput"):
        # xdist worker: reuse the directory created by the controller
        run_dir = Path(os.environ[_ENV_RUN_DIR])
    else:
        app_name = _detect_wallet_name(config)

        if app_name == "unknown":
            # Not a wallet session — `pytest base/tests/` and the like. Publishing a run directory,
            # an HTML report and a logcat capture for it would put a wallet-shaped result in
            # reports/ for something that never touched a wallet, and those stray dirs then invite
            # cleanup that can delete a live run (which is exactly how a real heidi run was
            # destroyed on 2026-09-02). Use a scratch directory and start no logcat.
            config._run_dir = Path(tempfile.mkdtemp(prefix="pytest-nonwallet-"))
            return

        # run_tests.py pre-creates a shared session dir and advertises it via
        # PYTEST_SESSION_DIR.  A direct pytest call creates its own directory.
        session_dir = os.environ.get(_ENV_SESSION_DIR)
        if session_dir:
            run_dir = Path(session_dir) / app_name
        else:
            timestamp = datetime.now().strftime(TIMESTAMP_FORMAT)
            run_dir = Path("reports") / timestamp / app_name

        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "screenshots").mkdir(exist_ok=True)
        os.environ[_ENV_RUN_DIR] = str(run_dir)

        # File log. Kept on `config` so pytest_sessionfinish can detach it again: runners/
        # run_tests.py calls pytest.main() in a loop inside one process, so this hook runs once
        # per wallet, and a handler left attached goes on receiving the next wallet's records.
        # Every wallet's test.log then contains every wallet that ran after it, which makes a log
        # line unusable as evidence about the wallet whose directory it sits in.
        handler = logging.FileHandler(str(run_dir / "test.log"))
        handler.setLevel(logging.DEBUG)
        handler.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT))
        logging.getLogger().addHandler(handler)
        config._log_handler = handler

        # HTML report — only if pytest-html is present and --html wasn't passed
        if hasattr(config.option, "htmlpath") and not config.option.htmlpath:
            config.option.htmlpath = str(run_dir / "report.html")
            config.option.self_contained_html = True

        # Device log — capture logcat for the duration of this wallet's session.
        #
        # This used to be written to `app.log`. It is not the app's log: it is the whole device's,
        # and `app.log` now holds the run's error-screen digest (one block per failed test, see
        # `record_error_screen`). Two different things were sharing one name, and the name
        # described neither.
        #
        # Not while merely collecting: `--collect-only` runs no test, but it used to clear and
        # stream the device's log and read its crash store — on a device someone else may be
        # testing on. It also ended the session in the same second, so the empty-capture guard
        # fired every time, and an alarm that means "your device log is broken" was raised by a
        # command that never intended to touch a device.
        if not getattr(config.option, "collectonly", False):
            start_logcat(config, run_dir)

    config._run_dir = run_dir


def pytest_sessionfinish(session, exitstatus):  # exitstatus required by pytest hookspec
    """Release this wallet's per-session resources: the logcat process and the file log."""
    config = session.config
    if hasattr(config, "workerinput"):
        return  # xdist workers don't own the logcat process
    logcat_proc = getattr(config, "_logcat_proc", None)
    # Captured **before** terminating: a healthy capture is still running at this point, so an
    # already-exited process means adb gave up during the session.
    died_early = logcat_proc is not None and logcat_proc.poll() is not None
    if logcat_proc is not None:
        logcat_proc.terminate()
        try:
            logcat_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            logcat_proc.kill()
    logcat_file = getattr(config, "_logcat_file", None)
    if logcat_file is not None:
        logcat_file.close()
        # Say so when the capture produced nothing. An empty logcat.log is indistinguishable from
        # a quiet device unless something checks, and that is exactly how a capture that had been
        # dead since at least 2026-06-01 went unnoticed: adb failed, the failure went to DEVNULL,
        # and a 0-byte file sat in every run directory looking like a log.
        try:
            path = Path(logcat_file.name)
            size = path.stat().st_size
            # `stderr` now goes **into** the file rather than to DEVNULL, which is what makes an
            # adb refusal visible at all — but it also means a failed capture is no longer empty.
            # "adb: error: more than one device/emulator" is ~60 bytes, so a size check alone would
            # call that a success and print a reassuring "0 KB captured". Judge it by whether adb
            # was still running and by what the file actually starts with.
            first = ""
            try:
                with path.open(encoding="utf-8", errors="replace") as f:
                    first = f.readline().strip()
            except Exception:
                pass
            # `startswith` only: a real logcat line can carry "error:" early — a short tag puts
            # it around column 30 in "09-11 10:00:00 E/X( 123): error: ..." — and `died_early`
            # already catches a refusal that produced no recognisable first line.
            refused = first.startswith(("error:", "adb:"))
            if size == 0 or died_early or refused:
                detail = (f" adb said: {first[:120]}" if first and refused else
                          " The adb process exited before the session ended." if died_early else
                          " Check that the configured device is attached (`adb devices`).")
                logger.warning(
                    f"[logcat] {path.name} did not capture the device log for this session "
                    f"({size} bytes).{detail}"
                )
            else:
                scope = getattr(config, "_logcat_scope", "scope unknown")
                logger.info(f"[logcat] {path.name}: {size // 1024} KB captured from {scope}")
        except Exception as e:
            logger.warning(f"[logcat] Could not check the captured device log: {e}")

    # Android's own crash/ANR store, read after the fact. Last, so it also catches anything the
    # teardown above provoked.
    target = getattr(config, "_dropbox_target", None)
    if target is not None:
        capture_dropbox(config._run_dir, *target)

    write_summary(config)

    # Say how many failures the digest recorded, for the same reason: "app.log is absent" has to
    # mean "no test failed" and not "the collector is broken again", and the only way to tell them
    # apart from the outside is for the run to have said so in test.log.
    run_dir = getattr(config, "_run_dir", None)
    if run_dir is not None:
        try:
            digest = run_dir / "app.log"
            if digest.exists():
                lines = digest.read_text(encoding="utf-8").splitlines()
                headers = [l for l in lines if l.startswith("--- TEST: ")]
                tests = len(set(headers))
                # Blocks are per *attempt*, so counting them alone would disagree with
                # summary.json, which reports one outcome per test however many tries it took.
                extra = f" across {tests} test(s)" if len(headers) != tests else ""
                logger.info(f"[conftest] app.log: {len(headers)} failure block(s){extra}")
            else:
                logger.info("[conftest] app.log: no failures to record this session")
        except Exception as e:
            logger.warning(f"[conftest] Could not summarise app.log: {e}")

    # Detach this wallet's file log, so the next pytest.main() in the same process starts with
    # only its own handler. Last, so anything logged above still reaches the file.
    log_handler = getattr(config, "_log_handler", None)
    if log_handler is not None:
        logging.getLogger().removeHandler(log_handler)
        log_handler.close()
        config._log_handler = None


# The Android `system` uid, always 1000. system_server logs ActivityManager / ActivityTaskManager /
# WindowManager, which is where a wallet's process being started, killed, crashing or going
# unresponsive is visible at all — hovi's crash loop was 537 restarts, each an ActivityManager
# line pair at `I` level.
_SYSTEM_UID = "1000"

# The only two tags removed from the device log, both because the same information is already
# recorded in full elsewhere — never because it looked uninteresting:
#
#   Finsky   Play Store install machinery. We drive those installs deliberately and `test.log`
#            narrates them step by step ("Play Store state: ready_to_install" -> "installed").
#   appium   the UiAutomator2 server's own device-side chatter, which is `appium.log`'s entire
#            subject and is kept there at full fidelity.
#
# Measured over the 2026-09-10 survey: 56% smaller, and not one line about any wallet lost. The
# list is deliberately short — a denylist that grows on "this looks like noise" is how evidence
# disappears, and the whole point of filtering by uid rather than by priority was that nothing
# should be dropped for looking unimportant. `*:V` after these keeps everything else.
_SILENCED_TAGS = ["Finsky:S", "appium:S"]

# Buffers are left at adb's default (main, system, crash), verified to carry the whole of hovi's
# crash: 565 FATAL EXCEPTION and 564 JavascriptException lines in the archived capture, taken with
# this same command.


def _app_uid(serial: str, package: str) -> str:
    """The Android uid of `package` on `serial`, or "" if it cannot be read.

    The uid, not the pid: this suite restarts the app under test constantly (terminate/activate
    between tests, `mobile: clearApp` on a reset), and a pid filter would go stale on the first
    restart while a uid is stable for the life of the install.
    """
    try:
        listing = subprocess.run(
            ["adb", "-s", serial, "shell", "pm", "list", "packages", "-U", package],
            capture_output=True, text=True, timeout=15,
        ).stdout
    except Exception as e:
        logger.warning(f"[logcat] Could not read the uid of {package}: {e}")
        return ""
    # "package:droidwallet.hovi.id uid:10351" — and `pm list packages` matches on substring, so a
    # package whose name contains another's (toppan ships both walletapp and superapp) can return
    # several lines. Take the exact one.
    for line in listing.splitlines():
        if line.startswith(f"package:{package} uid:"):
            return line.split("uid:", 1)[1].strip()
    return ""


def _logcat_command(serial: str, package: str) -> list:
    """The logcat argv for this wallet: its own process plus the system's view of it.

    "Filtered so there is only relevant info" is a statement about *whose* lines are relevant, not
    about how important each line is, and getting that the wrong way round loses evidence. A
    priority floor (`*:W`) was the first attempt and would have been wrong: unime's verification
    reds were proved from its own `identity_wallet::` logs, of which ~1500 are at D/I; authbound's
    issuance results are read from 2479 `D/EUDI Wallet PROD-RELEASE` lines carrying its HTTP
    traffic; 15 of the 21 `issueDocumentsFromOffer failure` lines are at D. A floor would have
    dropped all of it and kept a file that looked fine.

    A tag allowlist is the same trap by another route — the fleet spans React Native, Flutter and
    native apps, so it would have to name every wallet's own tag and would silently lose the logs
    of any wallet nobody remembered to add.

    Filtering by uid instead keeps **every priority** of the wallet's own output and of
    system_server's, and drops only other processes: measured on ZT322L348J, 13,031 device-wide
    lines become 6,424, and what goes is a MediaTek USB HAL, `r_submix`, `BufferPoolAccessor2.0`,
    `artd`, `libPowerHal` and the like — nothing about any wallet. Nothing can be lost here by
    omission, only by an explicit, reviewable decision to exclude a uid.

    Falls back to an unfiltered capture when the uid is unknown (the app is not installed yet on
    the very first run, before `test_install`): too much log is recoverable, too little is not.
    """
    base = ["adb", "-s", serial, "logcat", "-v", "time"]
    uid = _app_uid(serial, package) if package else ""
    if not uid:
        # Device-wide, because the app is not installed yet and has no uid to scope to. Only
        # `appium` is dropped here, never `Finsky`: this is the *install* window, so the Play
        # Store's own logs are the evidence for why an install failed, which is exactly the
        # question this window raises. Appium's chatter is redundant with appium.log in any
        # window, so it can go regardless of scope.
        return [*base, "appium:S", "*:V"]
    # `*:V` keeps the default at verbose, so the silenced tags below are the *only* thing removed
    # and anything new is kept. Verified on-device that a tag filterspec composes with `--uid`.
    return [*base, f"--uid={uid},{_SYSTEM_UID}", *_SILENCED_TAGS, "*:V"]


def _logcat_scope(argv: list) -> str:
    """A one-line description of what the capture was actually scoped to.

    Reported at session end rather than logged where the decision is made: `start_logcat` runs
    inside `pytest_configure`, before pytest's logging plugin lowers the root level, so an INFO
    record there is dropped and never reaches `test.log`. That left the scoping decision invisible
    in the artifacts — and whether a device log holds one app or the whole device changes how every
    line in it should be read, so it must not be something a reader has to infer.
    """
    uid = next((a for a in argv if a.startswith("--uid=")), "")
    if not uid:
        return ("the whole device — the app was not installed when the capture started, so its "
                "uid was unknown")
    silenced = ", ".join(a[:-2] for a in argv if a.endswith(":S"))
    scope = f"uid {uid[len('--uid='):]} (app + system)"
    return f"{scope}, minus {silenced}" if silenced else scope


def _logcat_target(app_name: str):
    """`(serial, package)` for this wallet's device log — `("", "")` if it cannot be determined.

    Resolved from the wallet's own merged config, so a wallet that overrides the device (heidi runs
    on the emulator via `HEIDI_DEVICE_NAME`) is captured from *its* device. The previous capture
    passed no serial at all: with more than one device attached adb refuses outright, which is why
    seven of eight wallets produced a 0-byte log — and on the one occasion the refusal did not
    happen, heidi's session captured the phone while heidi was testing on the emulator, its 374 KB
    the only non-empty one in the run and every byte of it from the wrong device.

    A log filed under the wrong device is worse than no log, so `("", "")` — capture nothing — is
    the honest answer whenever the device is not known for certain.
    """
    try:
        config = load_config(app_name)
    except Exception as e:
        logger.warning(f"[logcat] Could not read {app_name}'s config to find its device: {e}")
        return "", ""
    android = config.get("android", {})
    if android.get("devices"):
        # A parallel multi-device run: one capture cannot represent several devices, and picking
        # devices[0] would publish one device's log as if it were all of them.
        logger.warning("[logcat] Multiple devices configured — no device log for this session")
        return "", ""
    serial = android.get("udid") or android.get("device_name") or ""
    return serial, config.get("application", {}).get("package", "")


# A DropBox entry opens with "2026-09-09 14:26:05 data_app_crash (text, 10412 bytes)".
# Tags are not all lowercase: emulator-5560 carries SYSTEM_BOOT and SYSTEM_FSCK, and
# native crashes are SYSTEM_TOMBSTONE. A header that fails to match is not merely skipped —
# it is appended to whatever entry is currently being accumulated, so an unmatched tag can
# smuggle another app's text into a block attributed to this wallet.
_DROPBOX_ENTRY = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) ([A-Za-z0-9_]+) \(")
_DEVICE_CLOCK = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")


def _device_now(serial: str) -> str:
    """The device's own wall clock, as DropBox stamps its entries. "" if it cannot be read.

    Taken from the device rather than this machine because DropBox timestamps are device-local and
    the phone need not share the host's timezone — comparing across clock domains would silently
    select the wrong entries, which is worse than selecting none.
    """
    try:
        # One quoted shell word. `adb shell` re-splits its arguments on the device, so passing the
        # format as its own argv entry delivers `date +%Y-%m-%d` and silently loses the time —
        # which turns the session floor into a whole-day floor and sweeps in unrelated crashes.
        out = subprocess.run(["adb", "-s", serial, "shell", "date '+%Y-%m-%d %H:%M:%S'"],
                             capture_output=True, text=True, timeout=15)
        stamp = out.stdout.strip()
        if not _DEVICE_CLOCK.match(stamp):
            logger.warning(f"[dropbox] Unexpected device clock format {stamp!r} — "
                           "skipping the crash store rather than guessing its time window")
            return ""
        return stamp
    except Exception as e:
        logger.warning(f"[dropbox] Could not read the device clock: {e}")
        return ""


def capture_dropbox(run_dir, serial: str, package: str, since: str) -> None:
    """Write this wallet's crashes and ANRs from Android's DropBox to crashes.log. Never raises.

    DropBox is the one evidence source here that does not depend on us watching. `logcat.log`,
    `appium.log` and the screenshots are all captured live, so a crash that kills the session takes
    its own evidence with it; DropBox is written by the system and read afterwards, so it survives.
    It is also structured — each entry carries the process, uid, package **version** and the build
    fingerprint alongside the stack — which a logcat line does not.

    This is exactly what the 2026-09-09 hovi investigation needed and did not have: the phone's
    DropBox still holds six `droidwallet.hovi.id` entries whose stacks name
    `CredentialCard (address at index.android.bundle:...)`. One `dumpsys` would have answered it,
    instead of a 241 MB logcat archive and a live device session.

    ANRs come along for free: DropBox tags them `data_app_anr`. `/data/anr/` itself is listable but
    **not readable** without root (`cat` gives Permission denied), so this is the only route to
    them on an ordinary device.

    Filtering is client-side and by whole entry: `dumpsys dropbox --print <timestamp>` selects
    entries *at* that timestamp, not since it, so the argument is no use as a lower bound. That
    means the whole store is read into memory once per session — every entry the device holds,
    decoded as text. It is bounded by Android's own DropBox quota (1000 entries by default, and
    the phone's 82 came to ~1 MB), so it is a read, not a stream; if a device ever holds enough to
    matter, page it by `--file` instead.
    """
    if not serial or not package:
        return
    if not since:
        # Without a floor every historical entry would be captured and read as this session's.
        # The phone holds 80 hovi crashes going back days; filing those under one run would
        # manufacture a finding. No file is the honest outcome.
        logger.warning("[dropbox] Session start time unknown — not capturing the crash store")
        return
    try:
        out = subprocess.run(["adb", "-s", serial, "shell", "dumpsys", "dropbox", "--print"],
                             capture_output=True, text=True, timeout=120)
        if out.returncode != 0:
            logger.warning(f"[dropbox] dumpsys failed: {out.stderr.strip()[:200]}")
            return

        # Split into whole entries, keeping only those stamped at or after this session began.
        # Lexicographic comparison is exact for "YYYY-MM-DD HH:MM:SS".
        entries, current, keep = [], [], False
        for line in out.stdout.splitlines():
            match = _DROPBOX_ENTRY.match(line)
            if match:
                if keep:
                    entries.append("\n".join(current))
                current, keep = [line], bool(since) and match.group(1) >= since
            elif current:
                current.append(line)
        if keep:
            entries.append("\n".join(current))

        # Only this wallet's entries, matched on the fields that name the crashing process rather
        # than by substring: the device is shared, and another app's crash filed under this wallet
        # would be read as a finding about it. `_app_uid` takes the same care for the same reason
        # (toppan ships both `walletapp` and `superapp`), and a substring test here would reopen
        # exactly that trap the day two wallet packages share a prefix.
        # `Process:`/`Package:` cover data_app_crash and data_app_anr. A **tombstone** (native
        # crash — tag SYSTEM_TOMBSTONE, and the same text inside data_app_native_crash) carries
        # neither: it names the process as `Cmdline: <pkg>` and `>>> <pkg> <<<`. Anchoring on only
        # the first two recognised those entries and then dropped them, which lost native crashes
        # that the earlier substring test had kept. `>>> pkg <<<` is delimited on both sides, so
        # adding it keeps the prefix-collision property the anchoring exists for.
        pkg = re.escape(package)
        owner = re.compile(rf"^(?:Process|Package|Cmdline): {pkg}(?:\s|$)|>>> {pkg} <<<", re.M)
        mine = [e for e in entries if owner.search(e)]
        if not mine:
            logger.info("[dropbox] No crashes or ANRs recorded for this session")
            return
        (run_dir / "crashes.log").write_text("\n\n".join(mine) + "\n", encoding="utf-8")
        tags = ", ".join(sorted({_DROPBOX_ENTRY.match(e).group(2)
                                 for e in mine if _DROPBOX_ENTRY.match(e)}))
        logger.warning(f"[dropbox] crashes.log: {len(mine)} entry(ies) for {package} ({tags})")
    except Exception as e:
        logger.warning(f"[dropbox] Could not capture the crash store: {e}")


def start_logcat(config, run_dir) -> None:
    """Stream this wallet's device log to logcat.log for the whole session. Never raises."""
    serial, package = _logcat_target(_detect_wallet_name(config))
    if not serial:
        return
    try:
        subprocess.run(["adb", "-s", serial, "logcat", "-c"], capture_output=True, timeout=10)
        log_file = open(run_dir / "logcat.log", "w")  # closed in pytest_sessionfinish
        try:
            # stderr into the file, not DEVNULL. Discarding it is what hid the failure for four
            # months: adb prints "error: more than one device/emulator" and exits, but `Popen`
            # still succeeds, so the old `except` never fired and nothing looked at the result.
            argv = _logcat_command(serial, package)
            config._logcat_scope = f"{serial}, {_logcat_scope(argv)}"
            config._logcat_proc = subprocess.Popen(
                argv,
                stdout=log_file,
                stderr=subprocess.STDOUT,
            )
        except Exception:
            log_file.close()
            raise
        config._logcat_file = log_file
        # Read once here, not at session end: DropBox is filtered by entry timestamp, and the
        # floor has to be the moment the session began.
        config._dropbox_target = (serial, package, _device_now(serial))
    except Exception as e:
        logger.warning(f"[conftest] Could not start logcat capture: {e}")


def _resolve_device(config: dict, worker_id: str) -> dict:
    """Return {server, device_name, udid} for the given xdist worker.

    Single-device config (default):
        "server": "http://127.0.0.1:4723",
        "android": { "device_name": "emulator-5554", ... }

    Multi-device config (for parallel runs with -n auto / -n N):
        "android": {
            "devices": [
                {"device_name": "emulator-5554", "server": "http://127.0.0.1:4723"},
                {"device_name": "emulator-5556", "server": "http://127.0.0.1:4724"}
            ]
        }
    Worker gw0 → devices[0], gw1 → devices[1], etc.

    The returned `udid` defaults to `device_name` (Android ADB serials like
    `emulator-5554` work as both). It can be overridden per device with an
    explicit `udid` key.
    """
    devices = config["android"].get("devices")
    if devices:
        idx = int(worker_id[2:]) if worker_id.startswith("gw") else 0
        device = devices[idx % len(devices)]
        device_name = device["device_name"]
        return {
            "server": device["server"],
            "device_name": device_name,
            "udid": device.get("udid", device_name),
        }
    device_name = config["android"]["device_name"]
    return {
        "server": config["server"],
        "device_name": device_name,
        "udid": config["android"].get("udid", device_name),
    }


def _validate_device(device: dict) -> None:
    """Fail fast if device.json hasn't been configured for this machine.

    Rejects any leftover placeholder, not just one hard-coded sentinel: an unfilled
    "<device_name>" used to pass this check and then die inside Appium several steps later.
    """
    name = device.get("device_name") or ""
    if not name or (name.startswith("<") and name.endswith(">")) or "${" in name:
        raise pytest.UsageError(
            "config/device.json: 'android.device_name' is not set "
            f"(got {name!r}). Set DEVICE_NAME in the project's .env (see env.example) to the "
            "ADB serial of the device/emulator to use — run `adb devices` to list available "
            "serials (e.g. 'emulator-5554')."
        )


def pytest_runtest_setup(item):
    """Skip tests whose issuer/verifier is unreachable, checked once per session.

    Applies only to tests parametrized with ``issuer_name``.  Each base_url is
    probed at most once; results are cached on ``item.config._provider_health``.

    The URL comes from `provider_matrix` rather than the test module, which no longer holds a
    config of its own: which providers a wallet runs is decided in one place, so the probe has to
    ask that same place. The wallet name is already on the callspec, from the indirect `driver`
    param every one of these tests carries.
    """
    callspec = getattr(item, "callspec", None)
    params = getattr(callspec, "params", {})
    issuer_name = params.get("issuer_name")
    wallet = params.get("driver")
    if not issuer_name or not wallet:
        return

    issuer_cfg = provider_matrix(wallet).get(issuer_name, {})
    base_url = issuer_cfg.get("base_url")
    if not base_url:
        return  # Static config provider — no URL to check

    if not hasattr(item.config, "_provider_health"):
        item.config._provider_health = {}
    cache = item.config._provider_health

    if base_url not in cache:
        ok, reason = check_provider_reachable(base_url)
        cache[base_url] = (ok, reason)
        if ok:
            logger.info(f"[provider] {issuer_name} reachable: {base_url}")
        else:
            logger.warning(f"[provider] {issuer_name} unreachable ({base_url}): {reason}")

    ok, reason = cache[base_url]
    if not ok:
        pytest.skip(f"Provider '{issuer_name}' is unreachable: {reason}")


# Lifecycle order for every wallet's tests. pytest collects files alphabetically, which puts
# test_install and test_onboarding *after* the credential tests — backwards as a report reads,
# and it only happens to work because the `app` fixture installs and `navigate_to_home`
# onboards as a side effect of whichever test runs first. Cleanup stays last so it wipes
# credentials only once everything has run.
_MODULE_ORDER = (
    "test_install",
    "test_onboarding",
    "test_credential_issuance",
    "test_credential_verification",
    "test_cleanup",
)


def _module_of(nodeid: str) -> str:
    """Test module name from a nodeid, e.g. 'test_credential_issuance'.

    Must split on '::' before the path separator: parametrize IDs contain slashes
    (``test_credential_issuance.py::test_credential_issuance[hovi_issuer/credential_issuance]``),
    so splitting on '/' alone yields the test ID instead of the module.
    """
    return nodeid.split("::")[0].rsplit("/", 1)[-1].split(".")[0]


def pytest_collection_modifyitems(items):
    """Sort each wallet's tests into lifecycle order.

    Stable, and keyed only on the module, so a wallet with its own ordering hook (gataca groups
    credential tests by DID method to avoid slow DID switches) keeps that grouping within each
    phase. Modules not listed keep their relative order, at the end.
    """
    def phase(item):
        module = _module_of(item.nodeid)
        return _MODULE_ORDER.index(module) if module in _MODULE_ORDER else len(_MODULE_ORDER)

    items.sort(key=phase)


_REPORT_EXCLUDED_MODULES = {"test_onboarding", "test_install"}


def pytest_html_results_table_row(report, cells):
    """Exclude onboarding and install tests from the HTML report.

    They still run (needed as setup steps) but clutter the report
    with results that aren't meaningful to reviewers.
    """
    # report.nodeid looks like: wallets/heidi/tests/test_onboarding.py::test_onboard[...]
    module = _module_of(report.nodeid)
    if module in _REPORT_EXCLUDED_MODULES:
        cells.clear()


def write_summary(config) -> None:
    """Write summary.json: one machine-readable record per test. Never raises.

    Until now the only structured record of a run was a `data-jsonblob` attribute inside
    `report.html`, so anything wanting outcomes — the report generator, a triage script, this
    session's own emulator survey — had to scrape HTML for them. That is not a theoretical cost:
    reading the survey's results that way silently returned "?" for every row when the key shape
    turned out not to be what was assumed, and a missing field looked exactly like a missing test.

    Reruns collapse to the final outcome, which is what a summary means. `attempts` keeps the
    count, because a test that passed on its third try is not the same as one that passed first
    time and a summary that hides that is misleading.

    **Not written under xdist.** `_results` accumulates on each worker's own config and
    `pytest_sessionfinish` returns early for workers, so the controller has nothing to write and
    a parallel `-n` run produces no summary at all. Documented in `wallets/README.md` so a missing
    file is not read as "no tests ran"; fixing it means shipping records back via `workeroutput`.
    """
    results = getattr(config, "_results", None)
    run_dir = getattr(config, "_run_dir", None)
    if not results or run_dir is None:
        return
    try:
        tests = sorted(results.values(), key=lambda r: r["nodeid"])
        passed = sum(1 for t in tests if t["outcome"] == "passed")
        summary = {
            "wallet": _detect_wallet_name(config),
            "started": getattr(config, "_session_started_at", 0),
            "totals": {
                "tests": len(tests),
                "passed": passed,
                "failed": sum(1 for t in tests if t["outcome"] == "failed"),
                "error": sum(1 for t in tests if t["outcome"] == "error"),
                "skipped": sum(1 for t in tests if t["outcome"] == "skipped"),
                "reruns": sum(t["attempts"] - 1 for t in tests),
            },
            "tests": tests,
        }
        (run_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        logger.info(f"[conftest] summary.json: {passed}/{len(tests)} passed")
    except Exception as e:
        logger.warning(f"[conftest] Could not write summary.json: {e}")


@pytest.hookimpl(tryfirst=True, hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    rep = outcome.get_result()
    setattr(item, f"rep_{rep.when}", rep)

    if rep.when == "setup":
        # A rerun re-runs the *same* item object, and pytest-rerunfailures only relabels reports
        # as "rerun" after the protocol returns — so each attempt looks like a genuine failure and
        # `save_failure_artifacts` runs again, overwriting the screenshot and XML dump. The `app`
        # fixture already resets its own two flags per attempt; these four were not, so the digest
        # kept attempt 1's cause while the files on disk became attempt 3's, and a test that failed
        # once and then passed still left a failure block contradicting summary.json.
        item._attempt = getattr(item, "_attempt", 0) + 1
        for stale in ("_failure_line", "_failure_category", "_failure_where",
                      "_error_screen_logged"):
            if hasattr(item, stale):
                delattr(item, stale)

    # Keep the raised exception's own one-line message, and its outcome category when it carries
    # one (`base.outcome.FlowFailure` sets `.category`). The failure digest in app.log needs the
    # exception itself, not the report's formatted `longrepr`: the category is an attribute by
    # deliberate design in outcome.py — "so the report can group by it without matching on prose" —
    # and it is reachable here and nowhere later. Only the first failing phase is kept, so a
    # teardown error can't overwrite what the test actually failed on.
    if rep.failed and call.excinfo is not None and not hasattr(item, "_failure_line"):
        exc = call.excinfo.value
        item._failure_line = f"{type(exc).__name__}: {exc}".split("\n")[0].strip()
        item._failure_category = getattr(exc, "category", "")

        # Where it was raised, which is the only useful thing left when the exception carries no
        # message. Selenium's `TimeoutException` stringifies as "Message: \n<stacktrace>", so the
        # one-line form above collapses to a bare "TimeoutException: Message:" — the first real
        # run of the digest produced exactly that for procivis and it said nothing at all. A
        # `WebDriverWait` with no `message=` is common in this suite, so this is the normal case,
        # not an edge one.
        crash = getattr(getattr(rep, "longrepr", None), "reprcrash", None)
        where = ""
        if crash is not None:
            path = Path(str(crash.path))
            try:
                path = path.relative_to(Path.cwd())
            except ValueError:
                pass  # a site-packages frame; the absolute path is what there is
            where = f"{path}:{crash.lineno}"
        item._failure_where = where

    _record_result(item, rep)


def _record_result(item, rep) -> None:
    """Accumulate this test's outcome for summary.json. Never raises into the run.

    Keyed by nodeid, which is stable across rerun attempts, so a retried test stays one record and
    ends up carrying its final outcome plus how many attempts it took.
    """
    try:
        config = item.config
        results = getattr(config, "_results", None)
        if results is None:
            results = config._results = {}
        record = results.setdefault(item.nodeid, {
            "nodeid": item.nodeid,
            "name": item.name,
            "params": {},
            "outcome": "passed",
            "failed_in": "",
            "error": "",
            "category": "",
            # Summed across attempts, deliberately: a flaky test's real cost is all the time it
            # consumed, not just its last try. Every other field describes the final attempt —
            # `attempts` is what tells a reader the two are measuring different things.
            "duration": 0.0,
            "attempts": 0,
        })
        params = getattr(getattr(item, "callspec", None), "params", {}) or {}
        record["params"] = {k: str(v) for k, v in params.items() if k != "driver"}
        record["duration"] = round(record["duration"] + getattr(rep, "duration", 0.0), 2)
        if rep.when == "setup":
            record["attempts"] += 1
            # A retry starts clean: the previous attempt's verdict must not outlive it.
            record.update(outcome="passed", failed_in="", error="", category="")
        # A teardown error must not overwrite a verdict the test already reached — the same rule
        # `_failure_line` follows above. Without this, a test that failed `[rejected]` in `call`
        # and then hit a second exception in teardown is published as `error`/`teardown` while
        # still carrying the call phase's message, and `totals.failed` undercounts. A skip counts
        # as a verdict reached, so a failing teardown after one leaves the skip standing.
        if rep.failed and not (rep.when == "teardown" and record["outcome"] != "passed"):
            # pytest calls a failure outside the test body an Error, and the distinction matters:
            # it means the wallet never got as far as being tested.
            record["outcome"] = "failed" if rep.when == "call" else "error"
            record["failed_in"] = rep.when
            record["error"] = getattr(item, "_failure_line", "")
            record["category"] = getattr(item, "_failure_category", "")
        elif rep.skipped:
            # No phase condition: both skip routes in this repo fire during **setup** — the
            # unreachable-provider `pytest.skip` in `pytest_runtest_setup`, and the
            # `pytest.mark.skip` params from `base/test_cases.py`. Requiring `when == "call"`
            # meant every skip kept the "passed" default, so unreachable providers were published
            # as passes and `totals.skipped` was structurally always 0. A call-phase skip is an
            # xfail, which equally must not read as a pass.
            record["outcome"] = "skipped"
    except Exception:
        pass  # a summary is never worth failing a run over


def _validate_locale(device: dict, config: dict, pytest_config) -> None:
    """Fail fast if the device is not running in the expected language.

    The suite reads what wallets write on screen: six of the eight wallets expose no resource-id on
    any widget, so most locators match on English copy. A device in another language does not fail
    one case, it fails every wallet, and the published matrix would report a fleet-wide outage that
    is really a phone setting.

    Asserting rather than setting it. Appium can change a device's locale, but that restarts the app
    under test and is the kind of interception this suite avoids: what we publish should describe
    the device as an operator actually configured it. Same shape as the `requires_fingerprint`
    precondition in the authbound conftest.

    Checked once per session. Reading nothing is "don't know" and only warns; the run continues.
    """
    if getattr(pytest_config, "_locale_checked", False):
        return
    pytest_config._locale_checked = True

    expected = config.get("android", {}).get("expected_locale", "en")
    if not expected:
        return

    actual = device_locale(device.get("udid") or device.get("device_name") or "")
    if actual is None:
        logger.warning("[locale] Could not read the device locale — continuing")
        return

    if not actual.lower().startswith(expected.lower()):
        raise pytest.UsageError(
            f"Device locale is {actual!r} but the suite's locators expect {expected!r}.\n"
            "  Most wallets expose no resource-id, so their screens are matched on English text; "
            "in another language every wallet fails at once for reasons that have nothing to do "
            "with the wallets.\n"
            "  Set the device language to English, or override 'android.expected_locale' in "
            "config/device.json if you really mean to run in another language."
        )
    logger.info(f"[locale] Device locale {actual} matches expected {expected!r}")


@pytest.fixture
def driver(request):
    app_name = request.param
    config = load_config(app_name)
    worker_id = getattr(request.config, "workerinput", {}).get("workerid", "master")
    device = _resolve_device(config, worker_id)
    _validate_device(device)
    _validate_locale(device, config, request.config)

    opts = UiAutomator2Options()
    opts.platform_name = config["android"]["platform_name"]
    opts.device_name = device["device_name"]
    opts.udid = device["udid"]
    opts.automation_name = config["android"]["automation_name"]
    opts.no_reset = True

    device_pin = config["android"].get("device_pin", "")

    # Deliberately NOT setting appium:unlockKey / appium:unlockType.
    #
    # Those make Appium unlock during session creation, before any of our code runs, so there is
    # no way to check first whether the keyguard is actually up. On 2026-09-02 it decided the
    # phone was locked when it was not, typed the device PIN, and the keystrokes went into a
    # focused Google search box — which submitted the PIN as a web query. The failure surfaced
    # only as "The device has failed to be unlocked"; the leak was silent.
    #
    # `base.android.unlock_if_locked` does the same job after the session exists, and requires
    # both Appium and the window manager to agree the keyguard is up before typing anything.
    # It also keeps the `uiautomator` strategy, which is load-bearing: Appium's default
    # "locksettings" strategy unlocks by *deleting* the device lock and re-creating it
    # (`locksettings clear --old <pin>` then `set-pin`), and clearing the lock credential wipes
    # every enrolled fingerprint. Wallets holding credentials behind a biometric-backed key then
    # land in Android's fingerprint enrollment wizard, which no test can complete — see
    # wallets/authbound/flows/credential_flow.py. Diagnosed 2026-08-10 from appium.log.

    driver = webdriver.Remote(device["server"], options=opts)  # type: ignore

    # Unlock immediately, so everything downstream can assume a usable screen — this is the job
    # the unlockKey capability used to do, now under a guard we control.
    unlock_if_locked(driver, device_pin, device.get("udid", ""))

    yield driver
    driver.quit()


@pytest.fixture
def app(driver, request):
    """Install the app if needed, launch it, and return the ready BaseTest instance."""
    app_name = request.node.callspec.params["driver"]
    config = load_config(app_name)

    # An earlier test in this session started an update that never confirmed installed, so
    # the app under test may be mid-replacement. Refuse to run rather than report results
    # for an unknown build.
    update_failure = getattr(request.config, "_update_fatal", None)
    if update_failure:
        pytest.fail(f"Wallet update did not complete: {update_failure}", pytrace=False)

    recording_enabled = config.get("recording", {}).get("enabled", False)
    if recording_enabled:
        try:
            driver.start_recording_screen(timeLimit=180)
            logger.info("[recording] Screen recording started")
        except Exception as e:
            logger.warning(f"[recording] Failed to start recording: {e}")
            recording_enabled = False

    base_test = BaseTest(driver, config)
    just_installed = base_test.setup()

    app_package = config["application"]["package"]

    # Wake the device screen before activating the app — if the screen has auto-locked
    # between tests, activate_app succeeds but the lock screen holds current_package,
    # causing the foreground check below to time out.
    #
    # `mobile: wakeUpDevice` does NOT exist in this driver (it threw 182 times in a single run,
    # silently swallowed), so this guard never actually ran. Use supported calls, and unlock with
    # the uiautomator strategy — the default `locksettings` one clears the device lock, which
    # wipes enrolled fingerprints (see the driver capabilities above).
    try:
        driver.press_keycode(_KEYCODE_WAKEUP)
        unlock_if_locked(driver, device_pin_for(config),
                         driver.capabilities.get("udid", ""))
    except Exception as e:
        logger.warning(f"[app] Could not wake/unlock the screen: {e}")

    if not getattr(request.config, "_apps_cleared", False):
        _clear_recent_apps(driver)
        request.config._apps_cleared = True

    if just_installed and not getattr(request.config, "_update_checked", False):
        # A freshly installed app is by definition the current build.
        request.config._update_checked = True
        logger.info(f"[update] {app_package} was just installed — no update check needed")

    # Check the Play Store for a newer build — once per session, not per test.
    # `driver`/`app` are function-scoped, so an unguarded check would open the Play Store
    # before every single test.  Runs after the screen is awake (so the details page is what
    # gets read, not the lock screen) and before app_info.json is written (so the report
    # records the version actually tested).
    updates = config.get("updates", {})
    if updates.get("enabled", True) and not getattr(request.config, "_update_checked", False):
        request.config._update_checked = True
        try:
            request.config._update_result = base_test.check_for_updates(
                timeout=updates.get("timeout", 900),
            )
        except UpdateNotFinished as e:
            # The package is being replaced underneath the suite — fail the whole session
            # instead of reporting results for an app mid-swap.
            request.config._update_fatal = str(e)
            request.config._update_result = {"update_check_error": str(e)}
            pytest.fail(f"Wallet update did not complete: {e}", pytrace=False)
        except Exception as e:
            # Everything that can fail before the Update button is tapped (no Play Store
            # listing, store unreachable) leaves the installed build untouched, so the run
            # is still valid — warn and carry on.
            logger.warning(f"[update] Update check failed for {app_package}: {e}")
            request.config._update_result = {"update_check_error": str(e)}

    driver.activate_app(app_package)

    def _app_is_foreground(d):
        # Dismiss permission dialogs that can take focus immediately after activate_app.
        handle_permission_if_present(d)
        return d.current_package == app_package

    WebDriverWait(driver, config.get("timeouts", {}).get("default", 10)).until(
        _app_is_foreground,
        message=f"Expected {app_package} to be in foreground, got {driver.current_package}"
    )

    # Write app_info.json once per session (first test wins).
    if not getattr(request.config, "_app_info_written", False):
        device_serial = driver.capabilities.get("deviceName", "")
        info = get_app_info(app_package, device_serial)
        info["wallet"] = app_name
        info["platform"] = driver.capabilities.get("platformName", "unknown")
        info["platform_version"] = driver.capabilities.get("platformVersion", "unknown")
        info["device_name"] = driver.capabilities.get("deviceModel", device_serial)
        info.update(getattr(request.config, "_update_result", {}))
        (request.config._run_dir / "app_info.json").write_text(
            json.dumps(info, indent=2)
        )
        request.config._app_info_written = True
        logger.info(
            f"[app] {info['wallet']} — {info['package']} "
            f"v{info['version_name']} (build {info['version_code']}) "
            f"on {info['platform']} {info['platform_version']} ({info['device_name']})"
        )

    request.node._artifact_captured = False
    request.node._appium_captured = False
    yield base_test

    # `node_failed`, not `rep_call`, because this is the only capture point a setup error ever
    # reaches: a wallet's `_ensure_home` raises before its own yield, so its teardown — and the
    # `capture_failure_artifact` inside it — never runs, while this one does.
    if node_failed(request.node) and not getattr(request.node, "_artifact_captured", False):
        # One implementation, shared with the per-wallet teardown path. The copy that used to
        # live here took the screenshot *before* the XML dump — the wrong order on a screen with
        # FLAG_SECURE, where the dump is the only evidence that can be captured at all.
        save_failure_artifacts(driver, request, config, wallet=app_name)

    # Appium's log, for the same reason and from the same fallback position.
    #
    # `capture_appium_logs` used to live only in the per-wallet `teardown_test`, which never runs
    # when `_ensure_home` raises before its yield — so the failure mode that most needs the Appium
    # log was the only one that could not produce it. hovi's 2026-09-09 run was the whole wallet
    # dying in setup, and hovi is the one directory in that run with no appium.log at all while
    # every other wallet has 11-21 MB of it. Capturing here covers a setup error and
    # `skip_home_setup` alike; the flag keeps a normal test to exactly one block.
    #
    # After `save_failure_artifacts`, so the screenshot and XML calls appear in the log too.
    if not getattr(request.node, "_appium_captured", False):
        capture_appium_logs(driver, request, sanitize_test_name(request.node.name))

    if _is_anr_present(driver):
        logger.warning(f"[app] ANR detected for {app_package} — clearing app cache before retry")
        _clear_app_cache(driver, app_package)

    if recording_enabled:
        try:
            video_b64 = driver.stop_recording_screen()
            if video_b64:
                recordings_dir = request.config._run_dir / "recordings"
                recordings_dir.mkdir(exist_ok=True)
                test_name = sanitize_test_name(request.node.name)
                path = recordings_dir / f"{test_name}.mp4"
                path.write_bytes(base64.b64decode(video_b64))
                if screen_is_protected(request, app_name):
                    # FLAG_SECURE blanks the video as well as screenshots — confirmed 2026-09-04
                    # by extracting frames from a heidi recording: the wallet's own screens are
                    # solid black, only the launcher and systemui render. The file is kept because
                    # those non-app segments are real evidence (an unroutable deeplink lands on the
                    # launcher), but it must not be read as a recording of the wallet.
                    logger.info(
                        f"[recording] Saved: {path} — NOTE: {app_name} sets FLAG_SECURE, so the "
                        "wallet's own screens are blank in this video; only non-app screens render"
                    )
                else:
                    logger.info(f"[recording] Saved: {path}")
        except Exception as e:
            logger.warning(f"[recording] Failed to save recording: {e}")

    try:
        driver.press_keycode(3)  # HOME — let the app save state before clearing
    except Exception:
        pass
    _clear_recent_apps(driver)
