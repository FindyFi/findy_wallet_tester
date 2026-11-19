import logging
import time

from appium.webdriver.common.appiumby import AppiumBy
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

from wallets.paradym.pages import home_page
from wallets.paradym.pages.settings_page import SettingsPage

logger = logging.getLogger(__name__)

# Top-left hamburger menu button (first of two "Menu" buttons on the home screen). It and the
# drawer's Settings item below were found and tapped on paradym 1.23.0 (nightly
# 2026-11-11_22-00-03 appium.log, 02:46:11-02:46:13).
_menu_button = (AppiumBy.XPATH, '(//android.widget.Button[@content-desc="Menu"])[1]')

# Each drawer row is a non-clickable ViewGroup containing a clickable Button (icon)
# and a non-clickable View (label text).  Navigate via the label → parent → button.
_settings_nav_item = (AppiumBy.XPATH,
    '//android.view.View[@text="Settings"]/../android.widget.Button')

# Back presses allowed to get from Settings (and its drawer) to home, and how long each home
# check waits. Bounded because one press too many from home leaves the app.
_MAX_BACKS = 3
_HOME_CHECK = 1

# How often, and how far apart, the toggle is re-read after the tap: its thumb slides across, so
# the first read can land before it has.
_SETTLE_READS = 4
_SETTLE_PAUSE = 0.5

# File, inside the run's xml_dumps folder, that takes the Settings screen when the toggle is
# unreadable.
_SETTINGS_DUMP = "paradym_settings_dev_mode.xml"


def _open_settings(driver, timeout):
    """Open the navigation drawer and tap Settings. Raises RuntimeError on timeout."""
    try:
        WebDriverWait(driver, timeout).until(
            EC.element_to_be_clickable(_menu_button)
        ).click()
    except TimeoutException:
        raise RuntimeError(
            "[settings_flow] Menu button not found — "
            "ensure the app is on the home screen"
        )

    try:
        WebDriverWait(driver, timeout).until(
            EC.element_to_be_clickable(_settings_nav_item)
        ).click()
    except TimeoutException:
        raise RuntimeError(
            "[settings_flow] Settings item not found in navigation drawer"
        )


def _describe(error: Exception) -> str:
    """`Type: first line of the message`, so a multi-line driver error stays on one log line."""
    lines = [line.strip() for line in str(error).splitlines() if line.strip()]
    return f"{type(error).__name__}: {lines[0]}" if lines else type(error).__name__


def _save_settings_screen(driver, xml_dir) -> str:
    """Save the Settings screen's XML and return the clause for the warning that names it.

    Development Mode never fails a test, so the root conftest never dumps this screen, yet the
    toggle's locator is unverified on paradym 1.23.0 and a dump is the only way to correct it.
    Never raises: a failed save is reported in the clause instead.
    """
    try:
        xml_dir.mkdir(parents=True, exist_ok=True)
        (xml_dir / _SETTINGS_DUMP).write_text(driver.page_source, encoding="utf-8")
    except Exception as e:
        return f"; could not save the Settings screen ({_describe(e)})"
    return f"; Settings screen saved to xml_dumps/{_SETTINGS_DUMP}"


def _switch_on(driver, timeout, page_args, xml_dir=None):
    """Open Settings and turn Development Mode on if it is off.

    Returns (outcome, problem): outcome is "turned on" or "was already on", problem is None
    then, and otherwise the sentence for what could not be done. When the toggle's state cannot
    be read and `xml_dir` is given, the Settings screen is saved there. Raises when Settings
    cannot be reached; the caller turns that into a problem.
    """
    _open_settings(driver, timeout)

    settings = SettingsPage(driver, **page_args)
    settings.wait_until_loaded()

    state = settings.is_dev_mode_on()
    if state is None:
        problem = "could not read the Development Mode toggle's state"
        if xml_dir is not None:
            problem += _save_settings_screen(driver, xml_dir)
        return None, problem
    if state:
        return "was already on", None

    settings.enable_dev_mode()
    for _ in range(_SETTLE_READS):
        time.sleep(_SETTLE_PAUSE)
        if settings.is_dev_mode_on():
            return "turned on", None
    return None, "tapped the toggle but it did not read as on"


def _return_home(driver) -> bool:
    """Press back until paradym's home shows; never presses it while home is showing.

    Never raises: a dead session means "not home", and init_flow gets to deal with it.
    """
    try:
        for _ in range(_MAX_BACKS):
            if home_page.on_screen(driver, timeout=_HOME_CHECK):
                return True
            driver.back()
        return home_page.on_screen(driver, timeout=_HOME_CHECK)
    except Exception:
        return False


def ensure_dev_mode(driver, xml_dir=None, **page_args) -> bool:
    """Turn paradym's Development Mode on if it is off, then go back to its home screen.

    Development Mode makes paradym's error screens carry the "Development mode error" detail that
    the retry rules in flows/_failures.py read. A fresh install has it off. `xml_dir` (a Path, or
    None) is where the Settings screen is saved if the toggle's state cannot be read.

    Never raises: failing to turn it on costs detail on error screens, not the test. Logs exactly
    one line, and returns True iff paradym's home screen is showing at the end.
    """
    timeout = page_args.get("timeouts", {}).get("default", 10)

    done, problem = None, None
    try:
        done, problem = _switch_on(driver, timeout, page_args, xml_dir)
    except Exception as e:
        problem = _describe(e)

    at_home = _return_home(driver)

    stray = (
        "; paradym was not back on its home screen afterwards, so init_flow takes over"
        if not at_home else ""
    )
    if problem is None:
        logger.info(f"[settings_flow] paradym Development Mode: {done}{stray}")
    else:
        logger.warning(
            f"[settings_flow] Could not turn on paradym's Development Mode ({problem}) — "
            "continuing without it, so its error screens will lack the development-mode "
            f"detail{stray}"
        )
    return at_home


def ensure_dev_mode_once(driver, config, **page_args) -> bool:
    """`ensure_dev_mode` once per pytest session; True when it did nothing or ended on home.

    The flag is set before the attempt, so a failure is not retried by every later test: each
    attempt costs a few seconds of navigation and the answer would be the same.

    The Settings screen, if it has to be saved, goes to the run's xml_dumps folder; a config
    without a run directory saves nothing.
    """
    if getattr(config, "_paradym_dev_mode_checked", False):
        return True
    config._paradym_dev_mode_checked = True
    run_dir = getattr(config, "_run_dir", None)
    xml_dir = run_dir / "xml_dumps" if run_dir is not None else None
    return ensure_dev_mode(driver, xml_dir=xml_dir, **page_args)
