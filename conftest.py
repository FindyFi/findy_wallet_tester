import base64
import json
import logging
import os
import re
import subprocess
import time
import pytest
from datetime import datetime
from pathlib import Path
from typing import Optional


def _load_dotenv():
    """Load KEY=VALUE pairs from .env at the project root into os.environ.

    Values already set in the environment are not overwritten, so shell exports
    and CI/CD environment injection always take precedence over the .env file.
    """
    env_path = Path(__file__).parent / ".env"
    try:
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    except FileNotFoundError:
        pass


_load_dotenv()

from appium import webdriver
from appium.options.android.uiautomator2.base import UiAutomator2Options
from selenium.webdriver.support.ui import WebDriverWait

from base.android import handle_biometric_if_present, handle_permission_if_present
from base.base_test import BaseTest, UpdateNotFinished
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
            candidate = parts[parts.index("wallets") + 1]
            if candidate in known:
                return candidate
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

        # File log
        handler = logging.FileHandler(str(run_dir / "test.log"))
        handler.setLevel(logging.DEBUG)
        handler.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT))
        logging.getLogger().addHandler(handler)

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
    """Stop logcat capture after all tests complete."""
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


_ENV_PLACEHOLDER = re.compile(r"\$\{(\w+)\}")

# ${VAR:-default}: the whole value is one placeholder carrying its own fallback.
_ENV_DEFAULTED = re.compile(r"^\$\{(\w+):-([^}]*)\}$")

_TRUE = {"true", "yes", "on"}
_FALSE = {"false", "no", "off"}


def _coerce(text: str):
    """Turn an environment string into the JSON type it is standing in for.

    Environment variables are always strings, but the settings they now replace are booleans and
    numbers. Without this, `SKIP_IF_DONE=false` arrives as the string "false", which is **truthy**,
    so a wipe would be permanently on while looking configured.

    Numbers are parsed before the boolean words on purpose. "0" and "1" are both plausible numbers
    and plausible booleans, and reading them as booleans breaks the numeric settings:
    `${MAX_CREDENTIALS:-0}` would yield False, which then prints as "False" in every log line about
    the cleanup target. Left as ints they still behave correctly where a boolean is wanted, since
    Python already treats 0 as false and 1 as true.
    """
    stripped = text.strip()
    try:
        return int(stripped)
    except ValueError:
        pass
    try:
        return float(stripped)
    except ValueError:
        pass
    lowered = stripped.lower()
    if lowered in _TRUE:
        return True
    if lowered in _FALSE:
        return False
    return text


_SHARED_PREFIX = "DEFAULT_"


def _lookup(name: str, wallet: str = "") -> Optional[str]:
    """Resolve one placeholder name against the environment, wallet override first.

    A setting written in the JSON as ``${DEFAULT_RESET:-true}`` can be set three ways, in
    descending precedence:

        HOVI_RESET=false     per-wallet override; the wallet prefix marks it as wallet-specific
        DEFAULT_RESET=false  the shared default, applying to every wallet
        (neither set)        the literal default committed in the JSON

    Naming is the whole point of the convention: anything beginning `DEFAULT_` is fleet-wide,
    anything beginning with a wallet name applies to that wallet alone, and you can tell which is
    which from `.env` without reading any code.
    """
    if wallet and name.startswith(_SHARED_PREFIX):
        override = f"{wallet.upper()}_{name[len(_SHARED_PREFIX):]}"
        if os.environ.get(override):
            return os.environ[override]
    return os.environ.get(name)


def _expand_env(value, missing: list, where: str = "", wallet: str = ""):
    """Substitute ${VAR} and ${VAR:-default} from the environment through a config structure.

    Any value in any config file can reference an environment variable, so machine-specific
    settings live in the gitignored `.env` instead of in committed JSON.

    Two forms, and the difference matters:

    - ``${VAR}`` is **required**. Unset variables are collected in `missing` and reported together,
      rather than left as a literal "${VAR}" — that used to surface as a device named
      "${DEVICE_NAME}" and an Appium error three steps later. No wallet override is applied here:
      heidi already writes `${HEIDI_DEVICE_NAME}` explicitly, and silently falling back to the
      shared `DEVICE_NAME` would run heidi on the phone instead of its emulator.
    - ``${DEFAULT_X:-default}`` is **optional** and takes a per-wallet override (see `_lookup`).
      Unset means use the literal committed in the JSON, so the file stays meaningful and a machine
      opts in to a setting rather than every machine having to declare one. The result is
      type-coerced, so booleans and numbers survive the trip through the environment.
    """
    if isinstance(value, dict):
        return {k: _expand_env(v, missing, f"{where}.{k}" if where else k, wallet)
                for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env(v, missing, f"{where}[{i}]", wallet) for i, v in enumerate(value)]
    if isinstance(value, str):
        defaulted = _ENV_DEFAULTED.match(value.strip())
        if defaulted:
            name, fallback = defaulted.group(1), defaulted.group(2)
            # A quoted default declares "this setting is text, never coerce it". PINs are the
            # reason: "123456" must stay a string, because the pin pages type it digit by digit
            # and an int cannot be iterated (and "0123" would lose its leading zero).
            is_text = len(fallback) >= 2 and fallback[0] == fallback[-1] and fallback[0] in "\"'"
            override = _lookup(name, wallet)
            if override:
                return override if is_text else _coerce(override)
            return fallback[1:-1] if is_text else _coerce(fallback)
        expanded = os.path.expandvars(value)
        for name in _ENV_PLACEHOLDER.findall(expanded):
            missing.append(f"{name} (used by {where})")
        return expanded
    return value


def load_config(wallet_name):
    device = json.loads((Path("config") / "device.json").read_text())
    wallet = json.loads((Path("wallets") / wallet_name / "config.json").read_text())
    merged = {**device, **wallet}

    onboarding = merged.get("onboarding", {})
    if "skip_if_done" in onboarding:
        raise pytest.UsageError(
            f"wallets/{wallet_name}/config.json: 'onboarding.skip_if_done' has been replaced by "
            "'onboarding.reset', which reads the way an operator thinks about it: reset=true means "
            "wipe the wallet and onboard again. Rename the key and invert the value "
            "(skip_if_done: true becomes reset: false)."
        )

    missing = []
    merged = _expand_env(merged, missing, wallet=wallet_name)
    if missing:
        raise pytest.UsageError(
            f"Unset environment variable(s) referenced by the {wallet_name} config:\n  "
            + "\n  ".join(sorted(set(missing)))
            + "\nSet them in the project's .env file (see env.example) or export them."
        )

    # `reset` is the operator-facing spelling: true means "wipe and onboard again". The flows still
    # take `skip_if_done`, which is the same switch seen from the other side, so it is derived here
    # once rather than inverted at nine call sites. When the test bodies move into base/, the
    # internal name can follow and this line goes away.
    merged.setdefault("onboarding", {})["skip_if_done"] = not merged["onboarding"].get("reset", False)
    return merged


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
    """
    callspec = getattr(item, "callspec", None)
    issuer_name = getattr(callspec, "params", {}).get("issuer_name")
    if not issuer_name:
        return

    wallet_config = getattr(item.module, "_config", None)
    if not wallet_config:
        return

    issuer_cfg = wallet_config.get("test_cases", {}).get(issuer_name, {})
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
    if device_pin:
        opts.set_capability("appium:unlockType", "pin")
        opts.set_capability("appium:unlockKey", device_pin)
        # Unlock by typing the PIN on the keyguard, NOT via adb.
        #
        # Appium's default unlock strategy is "locksettings", which unlocks by *deleting* the
        # device lock and re-creating it:
        #     locksettings clear --old <pin>
        #     locksettings set-pin <pin>
        # Clearing the lock credential wipes every enrolled fingerprint (Android guarantees
        # that), and set-pin restores only the PIN. Any wallet that stores credentials behind a
        # biometric-backed key then finds no biometric enrolled and sends the run into Android's
        # fingerprint *enrollment* wizard, which no test can complete — see
        # wallets/authbound/flows/credential_flow.py.
        #
        # It only bites when a session starts with the screen locked, which is why it looked
        # intermittent: the log says "Screen already unlocked, doing nothing" on the runs that
        # were unaffected. Diagnosed 2026-08-10 from appium.log.
        opts.set_capability("appium:unlockStrategy", "uiautomator")

    driver = webdriver.Remote(device["server"], options=opts)  # type: ignore
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
        if device_pin_for(config) and driver.execute_script("mobile: isLocked"):
            driver.execute_script("mobile: unlock", {
                "key": device_pin_for(config),
                "type": "pin",
                "strategy": "uiautomator",
            })
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

    if (hasattr(request.node, "rep_call") and request.node.rep_call.failed
            and not getattr(request.node, "_artifact_captured", False)):
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
