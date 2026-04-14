import base64
import json
import logging
import os
import subprocess
import pytest
from datetime import datetime
from pathlib import Path


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

from base.base_test import BaseTest
from base.utils import list_wallets, TIMESTAMP_FORMAT, get_app_info, check_provider_reachable, sanitize_test_name

logger = logging.getLogger(__name__)

_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_ENV_RUN_DIR = "PYTEST_RUN_DIR"
_ENV_SESSION_DIR = "PYTEST_SESSION_DIR"


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


def load_config(wallet_name):
    device = json.loads((Path("config") / "device.json").read_text())
    wallet = json.loads((Path("wallets") / wallet_name / "config.json").read_text())
    return {**device, **wallet}


def _resolve_device(config: dict, worker_id: str) -> dict:
    """Return {server, device_name} for the given xdist worker.

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
    """
    devices = config["android"].get("devices")
    if devices:
        idx = int(worker_id[2:]) if worker_id.startswith("gw") else 0
        device = devices[idx % len(devices)]
        return {"server": device["server"], "device_name": device["device_name"]}
    return {"server": config["server"], "device_name": config["android"]["device_name"]}


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


_REPORT_EXCLUDED_MODULES = {"test_onboarding", "test_install"}


def pytest_html_results_table_row(report, cells):
    """Exclude onboarding and install tests from the HTML report.

    They still run (needed as setup steps) but clutter the report
    with results that aren't meaningful to reviewers.
    """
    # report.nodeid looks like: wallets/heidi/tests/test_onboarding.py::test_onboard[...]
    module = report.nodeid.split("/")[-1].split(".")[0]
    if module in _REPORT_EXCLUDED_MODULES:
        cells.clear()


@pytest.hookimpl(tryfirst=True, hookwrapper=True)
def pytest_runtest_makereport(item):
    outcome = yield
    rep = outcome.get_result()
    setattr(item, f"rep_{rep.when}", rep)


@pytest.fixture
def driver(request):
    app_name = request.param
    config = load_config(app_name)
    worker_id = getattr(request.config, "workerinput", {}).get("workerid", "master")
    device = _resolve_device(config, worker_id)

    opts = UiAutomator2Options()
    opts.platform_name = config["android"]["platform_name"]
    opts.device_name = device["device_name"]
    opts.automation_name = config["android"]["automation_name"]
    opts.no_reset = True

    driver = webdriver.Remote(device["server"], options=opts)  # type: ignore
    yield driver
    driver.quit()


@pytest.fixture
def app(driver, request):
    """Install the app if needed, launch it, and return the ready BaseTest instance."""
    app_name = request.node.callspec.params["driver"]
    config = load_config(app_name)

    recording_enabled = config.get("recording", {}).get("enabled", False)
    if recording_enabled:
        try:
            driver.start_recording_screen(timeLimit=180)
            logger.info("[recording] Screen recording started")
        except Exception as e:
            logger.warning(f"[recording] Failed to start recording: {e}")
            recording_enabled = False

    base_test = BaseTest(driver, config)
    base_test.setup()

    app_package = config["application"]["package"]
    driver.activate_app(app_package)

    WebDriverWait(driver, config.get("timeouts", {}).get("default", 10)).until(
        lambda d: d.current_package == app_package,
        message=f"Expected {app_package} to be in foreground, got {driver.current_package}"
    )

    # Write app_info.json once per session (first test wins).
    if not getattr(request.config, "_app_info_written", False):
        device_serial = driver.capabilities.get("deviceName", "")
        info = get_app_info(app_package, device_serial)
        info["wallet"] = app_name
        (request.config._run_dir / "app_info.json").write_text(
            json.dumps(info, indent=2)
        )
        request.config._app_info_written = True
        logger.info(
            f"[app] {info['wallet']} — {info['package']} "
            f"v{info['version_name']} (build {info['version_code']})"
        )

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
