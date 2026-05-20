import logging

from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

from base.utils import wait_present
from wallets.example.pages.landing_page import LandingPage, SCREEN_ID as _landing_id
from wallets.example.pages.home_page import SCREEN_ID as _home_id
from wallets.example.pages.pin_page import PinPage, HEADING as _pin_heading

logger = logging.getLogger(__name__)


def _detect_state(driver) -> str:
    """Return the current app state: 'landing', 'home', 'pin', or 'unknown'."""
    if wait_present(driver, _landing_id):
        return "landing"
    if wait_present(driver, _home_id):
        return "home"
    if wait_present(driver, _pin_heading):
        return "pin"
    # TODO: add checks for any other app-specific states (e.g. error screens, post-flow screens)
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


def _onboard(driver, pin: str, page_args: dict, default_timeout: float):
    """Complete the full onboarding flow from the landing screen to home.

    TODO: fill in the actual onboarding steps for this wallet.
    Use WebDriverWait(...).until(EC.presence_of_element_located(...)) between
    each step to wait for the next screen before interacting with it.
    """
    LandingPage(driver, **page_args).get_started()
    # TODO: add remaining onboarding steps (agreements, PIN setup, registration, etc.)
    WebDriverWait(driver, default_timeout).until(EC.presence_of_element_located(_home_id))
    logger.info("[init_flow] Onboarding complete — home screen reached")


def run(driver, pin: str, skip_if_done: bool = True, app_package: str = "", **page_args):
    timeouts = page_args.get("timeouts", {})
    default_timeout = timeouts.get("default", 10)
    package = app_package or driver.current_package

    state = _detect_state(driver)
    if state == "unknown":
        logger.info("[init_flow] App in intermediate state — pressing back to known screen")
        state = _back_to_known_state(driver, package)

    if state == "landing":
        logger.info("[init_flow] Fresh app state — running onboarding")
        _onboard(driver, pin, page_args, default_timeout)

    elif state == "home" and skip_if_done:
        logger.info("[init_flow] Already on home screen — skipping")
        return

    elif state == "pin" and skip_if_done:
        logger.info("[init_flow] PIN screen — logging in")
        PinPage(driver, **page_args).enter_pin(pin)
        try:
            WebDriverWait(driver, default_timeout).until(
                EC.invisibility_of_element_located(_pin_heading)
            )
        except TimeoutException:
            raise RuntimeError(
                f"PIN login failed — check 'pin' in config, "
                f"or set skip_if_done=false to auto-reset ({package})"
            )

    else:
        # skip_if_done=False (from any state): wipe app data and re-onboard from scratch
        logger.info(f"[init_flow] skip_if_done=false — clearing {package} and re-onboarding")
        driver.execute_script("mobile: clearApp", {"appId": package})
        driver.terminate_app(package)
        driver.activate_app(package)
        try:
            WebDriverWait(driver, default_timeout).until(
                EC.presence_of_element_located(_landing_id)
            )
        except TimeoutException:
            raise RuntimeError(
                f"Landing page not found after reset for {package}.\n"
                "  The app may have crashed or failed to launch after clearing data."
            )
        _onboard(driver, pin, page_args, default_timeout)
