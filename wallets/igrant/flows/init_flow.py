import logging

from appium.webdriver.common.appiumby import AppiumBy
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

from base.android import handle_biometric_if_present, handle_permission_if_present, BIOMETRIC_PROMPT
from base.utils import wait_present
from wallets.igrant.pages.home_page import SCREEN_ID as _home_id
from wallets.igrant.pages.landing_page import SCREEN_ID as _landing_id

logger = logging.getLogger(__name__)

# Shared Next/Finish button on all four onboarding screens
_NEXT_BTN = (AppiumBy.ID, "io.igrant.mobileagent:id/btNext")

# Unique identifiers for each onboarding step (used to detect which step we're on)
_REGION_SCREEN = (AppiumBy.ID, "io.igrant.mobileagent:id/rvRegion")
_RESTORE_SCREEN = (AppiumBy.ID, "io.igrant.mobileagent:id/tvRestore")
_SETTINGS_SCREEN = (AppiumBy.ID, "io.igrant.mobileagent:id/tvLanguageTitle")

# Biometric security toggle on the default settings screen — ON by default.
# Must be disabled during onboarding so subsequent app launches don't show a system biometric
# prompt that breaks the Appium session's current_package check.
_BIOMETRIC_TOGGLE = (AppiumBy.ID, "io.igrant.mobileagent:id/swSecurity")


def _detect_state(driver) -> str:
    """Return 'biometric_lock', 'onboarding', 'home', or 'unknown'."""
    # Dismiss any permission dialogs before checking — they create a separate window
    # context that hides the app's own UI elements from Appium's hierarchy.
    handle_permission_if_present(driver)
    if wait_present(driver, BIOMETRIC_PROMPT, timeout=0.5):
        return "biometric_lock"
    if wait_present(driver, _home_id):
        return "home"
    if wait_present(driver, _landing_id) or wait_present(driver, _RESTORE_SCREEN, timeout=2):
        return "onboarding"
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


def _onboard(driver, page_args: dict, default_timeout: float):
    """Tap through all remaining onboarding screens until the home screen appears.

    Checks each step in order and taps Next only if that screen is currently visible,
    so this is safe to call regardless of which step the onboarding is currently on.

    Steps: Welcome → Region (International pre-selected) → Restore (no backup) → Settings → Home
    """
    steps = [
        (_landing_id, "welcome"),
        (_REGION_SCREEN, "region"),
        (_RESTORE_SCREEN, "restore"),
    ]
    for screen_id, label in steps:
        if wait_present(driver, screen_id, timeout=3):
            logger.info(f"[init_flow] Onboarding: {label} screen — tapping Next")
            driver.find_element(*_NEXT_BTN).click()

    # Settings screen: disable biometric before finishing so subsequent launches don't prompt
    if wait_present(driver, _SETTINGS_SCREEN, timeout=3):
        logger.info("[init_flow] Onboarding: settings screen — disabling biometric, tapping Finish")
        try:
            toggle = driver.find_element(*_BIOMETRIC_TOGGLE)
            if toggle.get_attribute("checked") == "true":
                toggle.click()
        except Exception:
            pass
        driver.find_element(*_NEXT_BTN).click()

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

    if state == "biometric_lock":
        logger.info("[init_flow] Biometric lock — simulating fingerprint")
        try:
            handle_biometric_if_present(driver)
        except Exception as e:
            # mobile: fingerprint only works on emulators; on physical devices restart the app
            # so onboarding can re-run and the biometric toggle gets disabled this time.
            logger.warning(f"[init_flow] Biometric simulation failed ({e}) — restarting app")
            driver.terminate_app(package)
            driver.activate_app(package)
        # After biometric dismissal the app may take several seconds to settle;
        # poll more patiently than _detect_state's default 2 s window.
        try:
            WebDriverWait(driver, 8).until(
                lambda d: (
                    len(d.find_elements(*_home_id)) > 0
                    or len(d.find_elements(*_landing_id)) > 0
                )
            )
        except TimeoutException:
            pass
        state = _detect_state(driver)
        if state == "home":
            logger.info("[init_flow] Unlocked — home screen reached")
            return

    if state == "onboarding":
        logger.info("[init_flow] Onboarding in progress — completing flow")
        _onboard(driver, page_args, default_timeout)

    elif state == "home" and skip_if_done:
        logger.info("[init_flow] Already on home screen — skipping")
        return

    else:
        logger.info(f"[init_flow] skip_if_done=false — clearing {package} and re-onboarding")
        driver.execute_script("mobile: clearApp", {"appId": package})
        driver.terminate_app(package)
        driver.activate_app(package)
        # Clearing app data does not remove keystore entries — biometric may still appear.
        # Use try/except so physical-device fingerprint failure doesn't block onboarding.
        try:
            handle_biometric_if_present(driver)
        except Exception as e:
            logger.warning(f"[init_flow] Post-clear biometric failed ({e}) — continuing")
        # Wait patiently for landing page; the app may show a splash before onboarding.
        try:
            WebDriverWait(driver, default_timeout).until(
                EC.presence_of_element_located(_landing_id)
            )
        except TimeoutException:
            raise RuntimeError(
                f"Landing page not found after reset for {package}.\n"
                "  The app may have crashed or failed to launch after clearing data."
            )
        _onboard(driver, page_args, default_timeout)
