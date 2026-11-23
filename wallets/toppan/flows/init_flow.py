import logging
import time

from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

from base import outcome
from base.outcome import FlowFailure
from base.utils import wait_present
from wallets.toppan.pages import language_page, result_page
from wallets.toppan.pages.home_page import HomePage, SCREEN_ID as _home_id
from wallets.toppan.pages.language_page import LanguagePage

logger = logging.getLogger(__name__)

# How long a reset waits for the language modal or the home screen, whichever comes first.
RESET_WAIT = 60.0
HOME_RECHECK = 3.0


def _first_after_reset(driver, timeout: float) -> str:
    """"language", "home" or "" once `timeout` has run out. The modal is looked for first: while it
    is up the home screen is not in the accessibility tree. Every probe runs at least once."""
    end = time.monotonic() + timeout
    while True:
        if language_page.on_screen(driver, timeout=1):
            return "language"
        if wait_present(driver, _home_id, timeout=1):
            # A build may render home a moment before presenting the alert: look again before
            # accepting home, or the modal ends up over every test.
            if language_page.on_screen(driver, timeout=HOME_RECHECK):
                return "language"
            return "home"
        if time.monotonic() >= end:
            return ""


def _detect_state(driver, timeout: float = 2) -> str:
    """Return the current app state: 'home', 'language' or 'unknown'.

    The language modal is checked first: while it is up the home screen is not in the
    accessibility tree at all, so 'home' cannot be true at the same time — and a run that starts
    on it must not be sent through _back_to_known_state, which would press BACK eight times
    against a modal that ignores it.
    """
    if language_page.on_screen(driver, timeout=timeout):
        return "language"
    if wait_present(driver, _home_id, timeout=timeout):
        return "home"
    return "unknown"


def _back_to_known_state(driver, package: str) -> str:
    """Press back up to 8 times trying to reach a known state, then restart as last resort.

    toppan's verification result screen ignores BACK, so it is closed through its own "Back to
    Home Page" instead. The verification flow leaves it up on failure so the failure screenshot and
    dump show toppan's verdict; pressing BACK at it cost 50 s and an app restart on 2026-09-16.
    """
    for _ in range(8):
        if driver.current_package != package:
            logger.info("[init_flow] App backgrounded — re-activating")
            driver.activate_app(package)

        state = _detect_state(driver)
        if state != "unknown":
            logger.info(f"[init_flow] Reached known state: {state}")
            return state

        if result_page.on_screen(driver, timeout=1):
            logger.info("[init_flow] Verification result screen — tapping Back to Home Page")
            result_page.back_home(driver)
            continue

        driver.back()

    logger.warning("[init_flow] Back presses ineffective — restarting app")
    driver.terminate_app(package)
    driver.activate_app(package)
    state = _detect_state(driver)
    if state != "unknown":
        return state

    raise RuntimeError(
        "App stuck in unknown state even after restart — "
        "check for system dialogs or crashed screens"
    )


def _onboard(driver, default_timeout: float, **page_args):
    """Complete toppan's onboarding, which is choosing a language and confirming."""
    LanguagePage(driver, **page_args).select()
    logger.info(f"[init_flow] Selected {language_page.DEFAULT_LANGUAGE} — onboarding complete")


def run(driver, pin: str = "", skip_if_done: bool = True, app_package: str = "", **page_args):
    timeouts = page_args.get("timeouts", {})
    default_timeout = timeouts.get("default", 10)
    package = app_package or driver.current_package

    state = _detect_state(driver, timeout=default_timeout)
    if state == "unknown":
        logger.info("[init_flow] App in intermediate state — pressing back to known screen")
        state = _back_to_known_state(driver, package)

    if state == "language":
        # An earlier run was interrupted mid-onboarding, or the app was wiped outside the suite.
        logger.info("[init_flow] Language selection is showing — completing onboarding")
        _onboard(driver, default_timeout, **page_args)
        state = "home"

    if state == "home" and skip_if_done:
        logger.info("[init_flow] Already on home screen — skipping")
        return

    # skip_if_done=False: wipe app data and restart
    logger.info(f"[init_flow] skip_if_done=false — clearing {package} and restarting")
    driver.execute_script("mobile: clearApp", {"appId": package})
    driver.terminate_app(package)
    driver.activate_app(package)

    # A wiped toppan opens on its language modal, not on home, but the modal is slow: on
    # 2026-11-20 it was not seen ~12 s after the reset and up by ~22 s (both runs), the old
    # 10 s look gave up and expected home, and every test then ran under the modal. Wait for whichever shows first.
    reset_timeout = timeouts.get("reset", RESET_WAIT)
    shown = _first_after_reset(driver, reset_timeout)
    if shown == "language":
        _onboard(driver, default_timeout, **page_args)
    elif shown == "home":
        logger.info("[init_flow] No language selection after reset — home directly")
    else:
        raise FlowFailure(outcome.SETUP, (
            f"Home screen not found after reset for {package}.\n"
            f"  Neither the language selection nor the home screen showed within "
            f"{reset_timeout:.0f}s. The app may have crashed, or shown an onboarding screen this "
            "flow does not know about. Only the language selection is handled (see "
            "pages/language_page.py)."
        ))

    try:
        WebDriverWait(driver, default_timeout).until(
            EC.presence_of_element_located(_home_id)
        )
    except TimeoutException:
        raise FlowFailure(outcome.SETUP, (
            f"Home screen not found after reset for {package}.\n"
            "  The app may have crashed, or shown an onboarding screen this flow does not know "
            "about. Only the language selection is handled (see pages/language_page.py)."
        ))
    logger.info("[init_flow] App reset — home screen reached")
