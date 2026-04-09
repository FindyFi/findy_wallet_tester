import logging

from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

from base.android import handle_permission_if_present
from base.utils import wait_present
from wallets.hovi.pages.landing_page import LandingPage, SCREEN_ID as _landing_id
from wallets.hovi.pages.landing_page import on_screen as _landing_on_screen
from wallets.hovi.pages.home_page import SCREEN_ID as _home_id
from wallets.hovi.pages.home_page import on_screen as _home_on_screen

logger = logging.getLogger(__name__)

_secret_key_screen = ("xpath", '//*[@text="Secure Your Secret Key"]')
_acknowledge_checkbox = ("xpath", '//*[@text="I have copied and stored my secret key securely"]')
_access_wallet_btn = ("xpath", '//*[@text="Access My Wallet"]')


def _detect_state(driver, timeout: float = 2) -> str:
    """Return the current app state: 'landing', 'home', or 'unknown'."""
    if _home_on_screen(driver, timeout=timeout):
        return "home"
    if _landing_on_screen(driver, timeout=timeout):
        return "landing"
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
    state = _detect_state(driver, timeout=5)
    if state != "unknown":
        return state

    raise RuntimeError(
        "App stuck in unknown state even after restart — "
        "check for system dialogs or crashed screens"
    )


def _onboard(driver, pin: str, page_args: dict, default_timeout: float):
    """Complete the full onboarding flow from the landing screen to home.

    Sequence (Hovi Wallet):
      1. Landing  → tap "Create New Wallet"
      2. Secret key backup screen → tap checkbox → tap "Access My Wallet"
      3. Notification permission dialog (system) → handled by handle_permission_if_present
      4. Home screen reached.

    Note: Hovi uses a secret key (no PIN). The 'pin' parameter is ignored.
    """
    # Step 1 — Landing
    LandingPage(driver, **page_args).get_started()

    # Step 2 — Secret key backup
    WebDriverWait(driver, default_timeout).until(
        EC.presence_of_element_located(_secret_key_screen)
    )
    logger.info("[init_flow] Secret key screen — acknowledging and continuing")
    driver.find_element(*_acknowledge_checkbox).click()
    driver.find_element(*_access_wallet_btn).click()

    # Step 3 — Notification permission (system dialog, may or may not appear)
    handle_permission_if_present(driver)

    # Step 4 — Wait for home
    try:
        WebDriverWait(driver, default_timeout).until(
            EC.presence_of_element_located(_home_id)
        )
    except TimeoutException:
        raise RuntimeError("Hovi home screen not reached after onboarding")
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

    else:
        # skip_if_done=False: wipe app data and re-onboard from scratch
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
