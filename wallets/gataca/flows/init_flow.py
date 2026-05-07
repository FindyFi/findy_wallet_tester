import logging
import time

from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

from appium.webdriver.common.appiumby import AppiumBy

from base.android import handle_biometric_if_present
from base.utils import wait_present
from wallets.gataca.pages.landing_page import SCREEN_ID as _landing_id, LandingPage, has_open_wallet_button
from wallets.gataca.pages.home_page import SCREEN_ID as _home_id
from wallets.gataca.pages.pin_page import HEADING as _biometric_heading, on_screen as _biometric_on_screen

# Wallet-internal error dialog. Shown when the wallet's backend (connect.gataca.io /
# certify.gataca.io) returns an error or is unreachable. After biometric unlock, the
# wallet calls home; if that call fails, this dialog blocks the home screen.
_SERVICE_UNAVAILABLE = (AppiumBy.XPATH,
    '//*[contains(@text, "Service currently unavailable")]'
)

logger = logging.getLogger(__name__)


def _detect_state(driver) -> str:
    """Return the current app state: 'landing', 'home', 'biometric', or 'unknown'."""
    if wait_present(driver, _home_id):
        return "home"
    if wait_present(driver, _biometric_heading):
        return "biometric"
    if wait_present(driver, _landing_id):
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
    state = _detect_state(driver)
    if state != "unknown":
        return state

    raise RuntimeError(
        "App stuck in unknown state even after restart — "
        "check for system dialogs or crashed screens"
    )


def _unlock_biometric(driver, default_timeout: float):
    """Simulate fingerprint on the app-internal biometric unlock screen."""
    logger.info("[init_flow] Biometric unlock screen — simulating fingerprint")
    driver.execute_script("mobile: fingerprint", {"fingerprintId": 1})
    try:
        WebDriverWait(driver, default_timeout).until(
            EC.presence_of_element_located(_home_id)
        )
    except TimeoutException:
        if wait_present(driver, _SERVICE_UNAVAILABLE, timeout=1):
            raise RuntimeError(
                "Gataca wallet shows 'Service currently unavailable' after biometric unlock — "
                "the wallet's backend (connect.gataca.io / certify.gataca.io) is not responding. "
                "This is a wallet/backend issue, not an automation issue."
            )
        raise RuntimeError("Biometric unlock did not reach home screen within timeout")


def run(driver, pin: str, skip_if_done: bool = True, app_package: str = "", **page_args):
    timeouts = page_args.get("timeouts", {})
    default_timeout = timeouts.get("default", 10)
    package = app_package or driver.current_package

    # Handle Android system biometric prompt first (may appear over the app).
    handle_biometric_if_present(driver)

    state = _detect_state(driver)
    if state == "unknown":
        logger.info("[init_flow] App in intermediate state — pressing back to known screen")
        state = _back_to_known_state(driver, package)

    if state == "home" and skip_if_done:
        logger.info("[init_flow] Already on home screen — skipping")
        return

    elif state == "biometric" and skip_if_done:
        logger.info("[init_flow] App-internal biometric lock — unlocking")
        _unlock_biometric(driver, default_timeout)

    elif state == "landing":
        # Distinguish returning-user re-login ("Open your wallet") from fresh install.
        if has_open_wallet_button(driver):
            logger.info("[init_flow] Returning-user landing — tapping 'Open your wallet'")
            LandingPage(driver, **page_args).open_wallet()
            # Biometric screen sets Android secure flag — UiAutomator2 can't detect it.
            # Send fingerprint unconditionally after a short delay.
            time.sleep(2)
            logger.info("[init_flow] Sending fingerprint for biometric unlock")
            driver.execute_script("mobile: fingerprint", {"fingerprintId": 1})
            try:
                WebDriverWait(driver, default_timeout).until(
                    EC.presence_of_element_located(_home_id)
                )
            except TimeoutException:
                if wait_present(driver, _SERVICE_UNAVAILABLE, timeout=1):
                    raise RuntimeError(
                        "[init_flow] Gataca wallet shows 'Service currently unavailable' after "
                        "biometric unlock — the wallet's backend (connect.gataca.io / "
                        "certify.gataca.io) is not responding. This is a wallet/backend issue, "
                        "not an automation issue."
                    )
                raise RuntimeError("[init_flow] Biometric unlock did not reach home screen")
        else:
            raise RuntimeError(
                "Gataca is on the landing screen — manual onboarding required (email + OTP). "
                "Complete setup manually then re-run with skip_if_done=true."
            )

    else:
        # skip_if_done=False: wipe app data; requires manual re-onboarding afterwards.
        logger.info(f"[init_flow] skip_if_done=false — clearing {package}")
        driver.execute_script("mobile: clearApp", {"appId": package})
        driver.terminate_app(package)
        driver.activate_app(package)
        raise RuntimeError(
            "Gataca app data cleared — manual re-onboarding required (email + OTP). "
            "Complete setup manually then re-run with skip_if_done=true."
        )
