import logging

from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

from base.utils import wait_present
from wallets.toppan.pages.home_page import HomePage, SCREEN_ID as _home_id

logger = logging.getLogger(__name__)


def _detect_state(driver) -> str:
    """Return the current app state: 'home' or 'unknown'."""
    if wait_present(driver, _home_id):
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


def run(driver, pin: str = "", skip_if_done: bool = True, app_package: str = "", **page_args):
    timeouts = page_args.get("timeouts", {})
    default_timeout = timeouts.get("default", 10)
    package = app_package or driver.current_package

    state = _detect_state(driver)
    if state == "unknown":
        logger.info("[init_flow] App in intermediate state — pressing back to known screen")
        state = _back_to_known_state(driver, package)

    if state == "home" and skip_if_done:
        logger.info("[init_flow] Already on home screen — skipping")
        return

    # skip_if_done=False: wipe app data and restart
    logger.info(f"[init_flow] skip_if_done=false — clearing {package} and restarting")
    driver.execute_script("mobile: clearApp", {"appId": package})
    driver.terminate_app(package)
    driver.activate_app(package)
    try:
        WebDriverWait(driver, default_timeout).until(
            EC.presence_of_element_located(_home_id)
        )
    except TimeoutException:
        raise RuntimeError(
            f"Home screen not found after reset for {package}.\n"
            "  The app may have crashed or failed to launch after clearing data."
        )
    logger.info("[init_flow] App reset — home screen reached")
