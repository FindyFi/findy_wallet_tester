import logging

from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException
from appium.webdriver.common.appiumby import AppiumBy

from base.utils import wait_present
from wallets.paradym.flows import GO_TO_WALLET as _go_to_wallet
from wallets.paradym.pages.landing_page import LandingPage, SCREEN_ID as _get_started
from wallets.paradym.pages.home_page import SCREEN_ID as _home_heading
from wallets.paradym.pages import pin_page as pin_screen
from wallets.paradym.pages.pin_page import PinPage
from wallets.paradym.pages.biometrics_page import BiometricsPage
from wallets.paradym.pages.protect_data_page import ProtectDataPage

logger = logging.getLogger(__name__)

_repeat_heading = (AppiumBy.XPATH, '//*[@text="Repeat your PIN"]')
_camera_required = (AppiumBy.XPATH, '//*[@text="Please allow camera access"]')


def _detect_state(driver) -> str:
    """Return the current app state: 'landing', 'home', 'pin', 'camera_required', 'credential_success', or 'unknown'."""
    if wait_present(driver, _get_started):
        return "landing"
    if wait_present(driver, _home_heading):
        return "home"
    if pin_screen.on_screen(driver):
        return "pin"
    if wait_present(driver, _camera_required):
        return "camera_required"
    if wait_present(driver, _go_to_wallet):
        return "credential_success"
    return "unknown"


# Standard Android permission dialog dismiss buttons
_deny_permission = (AppiumBy.XPATH,
    '//*[@text="Don\'t allow" or @text="Deny" or @text="Deny & don\'t ask again"]')


def _dismiss_permission_dialog(driver):
    """Tap 'Don't allow' on any Android system permission dialog, if present."""
    try:
        btn = driver.find_element(*_deny_permission)
        btn.click()
        logger.info("[init_flow] Dismissed system permission dialog")
    except Exception:
        pass


def _tap_go_to_wallet(driver) -> bool:
    """Tap the 'Go to wallet' button if visible. Returns True if tapped."""
    try:
        driver.find_element(*_go_to_wallet).click()
        logger.info("[init_flow] Tapped 'Go to wallet'")
        return True
    except Exception:
        return False


def _back_to_known_state(driver, package: str) -> str:
    """Return to a known app state.

    Strategy:
    1. Dismiss any system permission dialog that may be blocking the UI.
    2. Tap 'Go to wallet' if present (error/completion screens) — instant recovery.
    3. Press Android back up to 8 times; check state after each press.
    4. If back presses push us out of the app, re-activate it and check state.
    5. Last resort: terminate and re-launch the app.
    """
    _dismiss_permission_dialog(driver)
    _tap_go_to_wallet(driver)

    for _ in range(8):
        if driver.current_package != package:
            logger.info("[init_flow] App backgrounded — re-activating")
            driver.activate_app(package)

        state = _detect_state(driver)
        if state != "unknown":
            logger.info(f"[init_flow] Reached known state: {state}")
            return state

        driver.back()

    # Last resort: restart the app
    logger.warning("[init_flow] Back presses ineffective — restarting app")
    driver.terminate_app(package)
    driver.activate_app(package)
    state = _detect_state(driver)
    if state != "unknown":
        logger.info(f"[init_flow] Post-restart state: {state}")
        return state

    raise RuntimeError(
        "App stuck in unknown state even after restart — "
        "check for system dialogs or crashed screens"
    )


def _navigate_to_home(driver, default_timeout: float):
    """Press Android back until the home screen is visible."""
    for _ in range(8):
        if wait_present(driver, _home_heading):
            return
        driver.back()
    if not wait_present(driver, _home_heading, timeout=default_timeout):
        raise RuntimeError(
            "Could not reach home screen — app may be stuck in an unexpected state"
        )


def _onboard(driver, pin: str, page_args: dict, default_timeout: float):
    LandingPage(driver, **page_args).get_started()
    PinPage(driver, **page_args).enter_pin(pin)
    WebDriverWait(driver, default_timeout).until(EC.presence_of_element_located(_repeat_heading))
    PinPage(driver, **page_args).enter_pin(pin)
    BiometricsPage(driver, **page_args).skip()
    ProtectDataPage(driver, **page_args).go_to_wallet()


def run(driver, pin: str, skip_if_done: bool = True, app_package: str = "", **page_args):
    timeouts = page_args.get("timeouts", {})
    default_timeout = timeouts.get("default", 10)
    package = app_package or driver.current_package

    state = _detect_state(driver)
    if state == "camera_required":
        raise RuntimeError(
            "App landed on 'Please allow camera access' — "
            "a previous test likely left the app in a broken state (e.g. incomplete verification flow)"
        )
    if state == "unknown":
        logger.info("[init_flow] App in intermediate state — pressing back to known screen")
        state = _back_to_known_state(driver, package)
    if state == "camera_required":
        raise RuntimeError(
            "App navigated to 'Please allow camera access' — "
            "a previous test likely left the app in a broken state (e.g. incomplete verification flow)"
        )

    if state == "credential_success":
        logger.info("[init_flow] Credential success screen — navigating to wallet home")
        _tap_go_to_wallet(driver)
        return

    if state == "landing":
        logger.info("[init_flow] Fresh app state — running full onboarding")
        _onboard(driver, pin, page_args, default_timeout)

    elif state == "home" and skip_if_done:
        logger.info("[init_flow] Already on home screen — skipping")
        return

    elif state == "pin" and skip_if_done:
        logger.info("[init_flow] PIN screen — logging in")
        PinPage(driver, **page_args).enter_pin(pin)
        try:
            WebDriverWait(driver, default_timeout).until(
                EC.invisibility_of_element_located(pin_screen.HEADING)
            )
        except TimeoutException:
            raise RuntimeError(
                f"PIN login failed — check 'pin' in config, "
                f"or set skip_if_done=false to auto-reset ({package})"
            )
        logger.info("[init_flow] PIN accepted — navigating to home screen")
        _navigate_to_home(driver, default_timeout)

    else:
        # skip_if_done=False (from any state): clear data and re-onboard
        logger.info(f"[init_flow] skip_if_done=false — clearing {package} and re-onboarding")
        driver.execute_script("mobile: clearApp", {"appId": package})
        driver.terminate_app(package)
        driver.activate_app(package)
        try:
            WebDriverWait(driver, default_timeout).until(EC.presence_of_element_located(_get_started))
        except TimeoutException:
            raise RuntimeError(
                f"Landing page not found after reset for {package}.\n"
                "  The app may have crashed or failed to launch after clearing data."
            )
        _onboard(driver, pin, page_args, default_timeout)
