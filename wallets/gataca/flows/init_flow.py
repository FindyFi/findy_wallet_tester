import logging

from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

from appium.webdriver.common.appiumby import AppiumBy

from base.android import handle_biometric_if_present, authenticate_with_pin
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


def _unlock_biometric(driver, pin, default_timeout: float):
    """Authenticate via PIN on the biometric unlock prompt and wait for home."""
    logger.info("[init_flow] Biometric unlock screen — authenticating with PIN")
    authenticate_with_pin(driver, pin)
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


def _unlock_from_landing(driver, page_args: dict, device_pin, default_timeout: float) -> bool:
    """Tap 'Open your wallet', authenticate the system prompt with PIN, and wait for home.

    Returns True if home was reached, False otherwise. A False here usually means the landing
    is stuck on a leftover "Sending your consent" OIDC overlay (from a prior test's deeplink),
    where 'Open your wallet' no longer raises the unlock prompt — the caller recovers by
    restarting the app, which clears that state.
    """
    logger.info("[init_flow] Returning-user landing — tapping 'Open your wallet'")
    LandingPage(driver, **page_args).open_wallet()
    # "Open your wallet" raises the system biometric prompt — authenticate with PIN. The
    # bottom-sheet can take a couple of seconds to surface, so use a wider detection window
    # than the speculative default.
    logger.info("[init_flow] Authenticating with PIN for biometric unlock")
    authenticate_with_pin(driver, device_pin, detect_timeout=default_timeout)
    try:
        WebDriverWait(driver, default_timeout).until(
            EC.presence_of_element_located(_home_id)
        )
        return True
    except TimeoutException:
        return False


def run(driver, pin: str, skip_if_done: bool = True, app_package: str = "", **page_args):
    timeouts = page_args.get("timeouts", {})
    default_timeout = timeouts.get("default", 10)
    device_pin = page_args.get("device_pin", "") or pin
    package = app_package or driver.current_package

    # Handle Android system biometric prompt first (may appear over the app) via PIN.
    authenticate_with_pin(driver, device_pin)

    state = _detect_state(driver)
    if state == "unknown":
        logger.info("[init_flow] App in intermediate state — pressing back to known screen")
        state = _back_to_known_state(driver, package)

    if state == "home" and skip_if_done:
        logger.info("[init_flow] Already on home screen — skipping")
        return

    elif state == "biometric" and skip_if_done:
        logger.info("[init_flow] App-internal biometric lock — unlocking")
        _unlock_biometric(driver, device_pin, default_timeout)

    elif state == "landing":
        # Distinguish returning-user re-login ("Open your wallet") from fresh install.
        if not has_open_wallet_button(driver):
            raise RuntimeError(
                "Gataca is on the landing screen — manual onboarding required (email + OTP). "
                "Complete setup manually then re-run with skip_if_done=true."
            )

        if _unlock_from_landing(driver, page_args, device_pin, default_timeout):
            return

        if wait_present(driver, _SERVICE_UNAVAILABLE, timeout=1):
            raise RuntimeError(
                "[init_flow] Gataca wallet shows 'Service currently unavailable' after "
                "biometric unlock — the wallet's backend (connect.gataca.io / "
                "certify.gataca.io) is not responding. This is a wallet/backend issue, "
                "not an automation issue."
            )

        # Landing unlock stalled — typically a leftover "Sending your consent" overlay from a
        # prior test's deeplink, where 'Open your wallet' no longer triggers the prompt. Restart
        # the app to clear it, then retry the unlock once.
        logger.warning("[init_flow] Landing unlock stalled — restarting app and retrying once")
        driver.terminate_app(package)
        driver.activate_app(package)
        authenticate_with_pin(driver, device_pin)

        state = _detect_state(driver)
        if state == "home":
            return
        if state == "landing" and has_open_wallet_button(driver) and \
                _unlock_from_landing(driver, page_args, device_pin, default_timeout):
            return
        raise RuntimeError("[init_flow] Biometric unlock did not reach home screen after app restart")

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
