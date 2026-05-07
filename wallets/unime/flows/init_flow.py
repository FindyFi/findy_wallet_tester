import logging
import time

from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

from base.utils import wait_present
from wallets.unime.pages.landing_page import LandingPage
from wallets.unime.pages.landing_page import SCREEN_ID as _landing_id
from wallets.unime.pages.landing_page import on_screen as _landing_on_screen
from wallets.unime.pages.home_page import SCREEN_ID as _home_id
from wallets.unime.pages.home_page import on_screen as _home_on_screen
from wallets.unime.pages.pin_page import PinPage
from wallets.unime.pages.pin_page import HEADING as _pin_heading
from wallets.unime.pages.pin_page import on_screen as _pin_on_screen

logger = logging.getLogger(__name__)

# Onboarding intermediate screen locators (not reused elsewhere, defined here).
_pledge_continue = (("xpath", '//*[@text="Continue"]'),)
_tc_row = ("xpath", '//*[@text="Terms & Conditions I have read and agree to the Terms & Conditions."]')
_data_row = ("xpath", '//*[@text="Data Ownership I understand that I am solely responsible for my data."]')
_tc_accept_btn = ("xpath", '//*[@text="Accept"]')          # inside T&C modal after scrolling
_customise_continue = ("xpath", '//*[@text="Continue"]')    # re-used "Continue" pattern
_password_input = ("xpath", '//android.widget.EditText[@hint="Enter a password"]')
_password_set_continue = ("xpath", '//*[@text="Continue"]')


def _detect_state(driver, timeout: float = 2) -> str:
    """Return the current app state: 'landing', 'home', 'pin', or 'unknown'."""
    if _home_on_screen(driver, timeout=timeout):
        return "home"
    if _pin_on_screen(driver, timeout=timeout):
        return "pin"
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


def _tap_continue(driver):
    driver.find_element("xpath", '//*[@text="Continue"]').click()


def _onboard(driver, pin: str, page_args: dict, default_timeout: float):
    """Complete the full onboarding flow from the landing screen to home.

    Sequence (discovered via live screen exploration):
      1. Landing  → tap "Create new profile"
      2. Pledge   → tap "Continue"
      3. T&C      → tap T&C row (opens modal) → scroll up in modal → tap "Accept"
                  → tap Data Ownership row → tap "Continue"
      4. Customisation (profile name) → tap "Continue" (keep default "Me")
      5. Password → type password into EditText → tap "Continue"
      6. Confirm password → type password again → tap "Continue"
      7. Password Set confirmation → tap "Continue"  (may time out — app navigates automatically)
      8. Home screen reached.
    """
    # Step 1 — Landing
    LandingPage(driver, **page_args).get_started()

    # Step 2 — Pledge
    WebDriverWait(driver, default_timeout).until(
        EC.presence_of_element_located(("xpath", '//*[@text="No funny business"]'))
    )
    logger.info("[init_flow] Pledge screen — continuing")
    _tap_continue(driver)

    # Step 3 — T&C checkboxes + modal
    WebDriverWait(driver, default_timeout).until(
        EC.presence_of_element_located(("xpath", '//*[@text="Terms & Conditions"]'))
    )
    logger.info("[init_flow] T&C screen — accepting terms")
    # Tap the T&C row — this opens a modal with the full terms text.
    driver.find_element(*_tc_row).click()
    # Scroll up in the modal to reach the Accept button at the bottom.
    from base.base_page import BasePage
    _page = BasePage(driver, **page_args)
    _page.swipe_up()
    _page.swipe_up()
    _page.swipe_up()
    WebDriverWait(driver, default_timeout).until(
        EC.presence_of_element_located(("xpath", '//*[@text="Accept"]'))
    )
    driver.find_element("xpath", '//*[@text="Accept"]').click()
    # Now tap the Data Ownership row.
    WebDriverWait(driver, default_timeout).until(
        EC.presence_of_element_located(("xpath", '//*[@text="Data Ownership I understand that I am solely responsible for my data."]'))
    )
    driver.find_element("xpath", '//*[@text="Data Ownership I understand that I am solely responsible for my data."]').click()
    _tap_continue(driver)

    # Step 4 — Customisation (profile name — keep default)
    WebDriverWait(driver, default_timeout).until(
        EC.presence_of_element_located(("xpath", '//*[@text="Let\'s go! Choose a profile name"]'))
    )
    logger.info("[init_flow] Customisation screen — keeping default name")
    _tap_continue(driver)

    # Step 5 — Set password
    WebDriverWait(driver, default_timeout).until(
        EC.presence_of_element_located(("xpath", '//android.widget.EditText[@hint="Enter a password"]'))
    )
    logger.info("[init_flow] Password screen — entering password")
    driver.find_element("xpath", '//android.widget.EditText[@hint="Enter a password"]').click()
    driver.execute_script("mobile: type", {"text": pin})
    _tap_continue(driver)

    # Step 6 — Confirm password (hint differs from step 5: "Retype your password")
    WebDriverWait(driver, default_timeout).until(
        EC.presence_of_element_located(("xpath", '//android.widget.EditText[@hint="Retype your password"]'))
    )
    logger.info("[init_flow] Confirm password screen — re-entering password")
    driver.find_element("xpath", '//android.widget.EditText[@hint="Retype your password"]').click()
    driver.execute_script("mobile: type", {"text": pin})
    _tap_continue(driver)

    # Step 7 — Fingerprint enrollment prompt (appears right after password confirmation).
    try:
        WebDriverWait(driver, default_timeout).until(
            EC.presence_of_element_located(("xpath", '//*[@text="Enable fingerprint"]'))
        )
        logger.info("[init_flow] Fingerprint prompt — dismissing")
        driver.find_element("xpath", '//*[@text="Decide later"]').click()
    except TimeoutException:
        pass  # prompt did not appear

    # Step 7b — Password Set Lottie animation.
    # The app plays a Lottie animation which briefly blocks UiAutomator2's
    # accessibility tree. Sleep first, then try to tap Continue if screen is still showing.
    time.sleep(3)
    try:
        WebDriverWait(driver, default_timeout).until(
            EC.presence_of_element_located(("xpath", '//*[@text="Password Set"]'))
        )
        logger.info("[init_flow] Password Set screen — continuing")
        driver.find_element("xpath", '//*[@text="Continue"]').click()
    except TimeoutException:
        pass  # app may have auto-navigated past the animation

    # Step 8 — Wait for home
    try:
        WebDriverWait(driver, default_timeout).until(
            EC.presence_of_element_located(_home_id)
        )
    except TimeoutException:
        raise RuntimeError("UniMe home screen not reached after onboarding")
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

    elif state == "pin":
        logger.info("[init_flow] Password lock screen — unlocking")
        PinPage(driver, **page_args).enter_pin(pin)
        try:
            WebDriverWait(driver, default_timeout).until(
                EC.invisibility_of_element_located(_pin_heading)
            )
        except TimeoutException:
            raise RuntimeError(
                f"Password unlock failed — check 'pin' in config "
                f"(must be 8+ chars with upper, lower, digit), "
                f"or set skip_if_done=false to auto-reset ({package})"
            )

    else:
        # skip_if_done=False from any state: wipe app data and re-onboard from scratch.
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
