import logging

from appium.webdriver.common.appiumby import AppiumBy
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException
from base.utils import wait_present
from wallets.procivis.pages.home_page import SCREEN_ID as _home_heading
from wallets.procivis.pages.landing_page import LandingPage, SCREEN_ID as _landing_id
from wallets.procivis.pages.user_agreement_page import UserAgreementPage, SCREEN_ID as _agreement_id
from wallets.procivis.pages.security_page import SecurityPage, SCREEN_ID as _security_id
from wallets.procivis.pages.pin_page import PinPage, INIT_SCREEN_ID as _pin_init_id, CONFIRM_TITLE_ID as _pin_confirm_id, biometric_on_screen, LOGIN_SCREEN_ID as _pin_login_id
from wallets.procivis.pages.wallet_registration_page import WalletRegistrationPage, SCREEN_ID as _registration_id

logger = logging.getLogger(__name__)

# Bottom Close button on InvitationProcessScreen — dismisses the entire screen back to home
_invitation_close = (AppiumBy.XPATH, '//*[@content-desc="Close" and @clickable="true"]')


def _close_invitation_screen(driver) -> bool:
    """Tap the Close button on InvitationProcessScreen if present. Returns True if tapped."""
    try:
        driver.find_element(*_invitation_close).click()
        logger.info("[init_flow] Closed InvitationProcessScreen")
        return True
    except Exception:
        return False


def _detect_state(driver) -> str:
    """Return the current app state: 'landing', 'home', 'pin_lock', or 'unknown'."""
    if wait_present(driver, _landing_id):
        return "landing"
    if wait_present(driver, _home_heading):
        return "home"
    if biometric_on_screen(driver) or wait_present(driver, _pin_login_id):
        return "pin_lock"
    return "unknown"


def _back_to_known_state(driver, package: str) -> str:
    """Press back up to 8 times trying to reach a known state."""
    _close_invitation_screen(driver)

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
    """Complete the Procivis onboarding flow."""
    LandingPage(driver, **page_args).create_new_wallet()

    WebDriverWait(driver, default_timeout).until(
        EC.presence_of_element_located(_agreement_id)
    )
    UserAgreementPage(driver, **page_args).accept()

    WebDriverWait(driver, default_timeout).until(
        EC.presence_of_element_located(_security_id)
    )
    SecurityPage(driver, **page_args).continue_to_pin()

    WebDriverWait(driver, default_timeout).until(
        EC.presence_of_element_located(_pin_init_id)
    )
    PinPage(driver, **page_args).enter_pin(pin)

    # Same screen reused for confirmation — title changes to "Confirm PIN code"
    WebDriverWait(driver, default_timeout).until(
        EC.presence_of_element_located(_pin_confirm_id)
    )
    PinPage(driver, **page_args).enter_pin(pin)

    # Wallet registers with the backend — may show an error and require retry
    WebDriverWait(driver, default_timeout).until(
        EC.presence_of_element_located(_registration_id)
    )
    WalletRegistrationPage(driver, **page_args).wait_for_completion()

    # Use a longer timeout here: after registration the app may show a brief
    # welcome animation before the home screen appears.
    timeouts = page_args.get("timeouts", {})
    post_registration_timeout = timeouts.get("credential_offer", default_timeout * 3)
    WebDriverWait(driver, post_registration_timeout).until(
        EC.presence_of_element_located(_home_heading)
    )
    logger.info("[init_flow] Onboarding complete — wallet home screen reached")


def run(driver, pin: str, skip_if_done: bool = True, app_package: str = "", **page_args):
    timeouts = page_args.get("timeouts", {})
    default_timeout = timeouts.get("default", 10)
    package = app_package or driver.current_package

    state = _detect_state(driver)
    if state == "unknown":
        logger.info("[init_flow] App in intermediate state — pressing back to known screen")
        state = _back_to_known_state(driver, package)

    if state == "pin_lock":
        logger.info("[init_flow] App locked — unlocking with PIN")
        page = PinPage(driver, **page_args)
        if biometric_on_screen(driver):
            page.dismiss_biometric_prompt()
        page.enter_login_pin(pin)
        WebDriverWait(driver, default_timeout).until(
            EC.presence_of_element_located(_home_heading)
        )
        logger.info("[init_flow] Unlocked — home screen reached")
        return

    if state == "landing":
        logger.info("[init_flow] Fresh app state — running onboarding")
        _onboard(driver, pin, page_args, default_timeout)

    elif state == "home" and skip_if_done:
        logger.info("[init_flow] Already on home screen — skipping")
        return

    else:
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
