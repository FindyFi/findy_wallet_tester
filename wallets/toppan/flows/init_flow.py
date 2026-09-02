import logging

from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

from base.utils import wait_present
from wallets.toppan.pages import language_page
from wallets.toppan.pages.home_page import HomePage, SCREEN_ID as _home_id
from wallets.toppan.pages.language_page import LanguagePage

logger = logging.getLogger(__name__)


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
    """Press back up to 8 times trying to reach a known state, then restart as last resort."""
    for _ in range(8):
        if driver.current_package != package:
            logger.info("[init_flow] App backgrounded — re-activating")
            driver.activate_app(package)

        state = _detect_state(driver)
        if state != "unknown":
            logger.info(f"[init_flow] Reached known state: {state}")
            return state

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

    # A wiped toppan opens on its language modal, not on home. Onboarding is picking a language.
    if language_page.on_screen(driver, timeout=default_timeout):
        _onboard(driver, default_timeout, **page_args)
    else:
        logger.info("[init_flow] No language selection after reset — expecting home directly")

    try:
        WebDriverWait(driver, default_timeout).until(
            EC.presence_of_element_located(_home_id)
        )
    except TimeoutException:
        raise RuntimeError(
            f"Home screen not found after reset for {package}.\n"
            "  The app may have crashed, or shown an onboarding screen this flow does not know "
            "about. Only the language selection is handled (see pages/language_page.py)."
        )
    logger.info("[init_flow] App reset — home screen reached")
