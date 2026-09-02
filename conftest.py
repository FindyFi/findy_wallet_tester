import base64
import json
import logging
import os
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
from base.conftest_helpers import node_failed
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

        # App log — capture logcat for the duration of the session
        try:
            subprocess.run(["adb", "logcat", "-c"], capture_output=True, timeout=5)
            app_log = open(run_dir / "app.log", "w")  # Kept open for the whole session; closed in pytest_sessionfinish
            try:
                logcat_proc = subprocess.Popen(
                    ["adb", "logcat", "-v", "time"],
                    stdout=app_log,
                    stderr=subprocess.DEVNULL,
                )
            except Exception:
                app_log.close()
                raise
            config._logcat_proc = logcat_proc
            config._logcat_file = app_log
        except Exception as e:
            logger.warning(f"[conftest] Could not start logcat capture: {e}")

    config._run_dir = run_dir


def pytest_sessionfinish(session, exitstatus):  # exitstatus required by pytest hookspec
    """Release this wallet's per-session resources: the logcat process and the file log."""
    config = session.config
    if hasattr(config, "workerinput"):
        return  # xdist workers don't own the logcat process
    logcat_proc = getattr(config, "_logcat_proc", None)
    if logcat_proc is not None:
        logcat_proc.terminate()
        try:
            logcat_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            logcat_proc.kill()
    logcat_file = getattr(config, "_logcat_file", None)
    if logcat_file is not None:
        logcat_file.close()

    # Detach this wallet's file log, so the next pytest.main() in the same process starts with
    # only its own handler. Last, so anything logged above still reaches the file.
    log_handler = getattr(config, "_log_handler", None)
    if log_handler is not None:
        logging.getLogger().removeHandler(log_handler)
        log_handler.close()
        config._log_handler = None


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


@pytest.hookimpl(tryfirst=True, hookwrapper=True)
def pytest_runtest_makereport(item):
    outcome = yield
    rep = outcome.get_result()
    setattr(item, f"rep_{rep.when}", rep)


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
    yield base_test

    # `node_failed`, not `rep_call`, because this is the only capture point a setup error ever
    # reaches: a wallet's `_ensure_home` raises before its own yield, so its teardown — and the
    # `capture_failure_artifact` inside it — never runs, while this one does.
    if node_failed(request.node) and not getattr(request.node, "_artifact_captured", False):
        reporting = config.get("reporting", {})
        test_name = sanitize_test_name(request.node.name)
        if reporting.get("screenshot_on_failure", True):
            screenshot_dir = request.config._run_dir / "screenshots"
            screenshot_dir.mkdir(parents=True, exist_ok=True)
            path = screenshot_dir / f"{test_name}.png"
            try:
                driver.save_screenshot(str(path))
                logger.info(f"[screenshot] Saved: {path}")
            except Exception as e:
                logger.warning(f"[screenshot] Failed to save screenshot: {e}")
        if reporting.get("xml_on_failure", False):
            xml_dir = request.config._run_dir / "xml_dumps"
            xml_dir.mkdir(parents=True, exist_ok=True)
            path = xml_dir / f"{test_name}.xml"
            try:
                path.write_text(driver.page_source, encoding="utf-8")
                logger.info(f"[xml] Saved: {path}")
            except Exception as e:
                logger.warning(f"[xml] Failed to save XML dump: {e}")

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
                logger.info(f"[recording] Saved: {path}")
        except Exception as e:
            logger.warning(f"[recording] Failed to save recording: {e}")

    try:
        driver.press_keycode(3)  # HOME — let the app save state before clearing
    except Exception:
        pass
    _clear_recent_apps(driver)
