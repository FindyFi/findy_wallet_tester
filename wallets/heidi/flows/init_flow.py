import logging

from appium.webdriver.common.appiumby import AppiumBy
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

from base.utils import wait_present
from base.android import BIOMETRIC_PROMPT as _BIOMETRIC_PROMPT, handle_biometric_if_present
from wallets.heidi.pages.landing_page import LandingPage, SCREEN_ID as _landing_id
from wallets.heidi.pages.privacy_page import PrivacyPage, SCREEN_ID as _privacy_id
from wallets.heidi.pages.security_page import (
    SecurityPage,
    SCREEN_ID as _security_id,
    ACTIVATION_FAILED_ID as _activation_failed_id,
)
from wallets.heidi.pages.home_page import SCREEN_ID as _home_id

logger = logging.getLogger(__name__)

_KEYCODE_ENTER = 66  # Android keycode for the ENTER / confirm key

# Android Settings PIN confirmation shown when authorizing biometric activation.
_SETTINGS_PKG = "com.android.settings"
_SETTINGS_PIN_ENTRY = (AppiumBy.ID, "com.android.settings:id/password_entry")


def _detect_state(driver, timeout: float = 2) -> str:
    """Return the current app state: 'home', 'biometric_unlock', 'landing', 'privacy', 'security', or 'unknown'."""
    # Home is the common steady state — check it first for speed.
    if wait_present(driver, _home_id, timeout=timeout):
        return "home"
    # Biometric prompt overlays the app; check before other app screens.
    if wait_present(driver, _BIOMETRIC_PROMPT, timeout=timeout):
        return "biometric_unlock"
    if wait_present(driver, _landing_id, timeout=timeout):
        return "landing"
    if wait_present(driver, _privacy_id, timeout=timeout):
        return "privacy"
    if wait_present(driver, _security_id, timeout=timeout):
        return "security"
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


def _activate_biometric(driver, page_args: dict, default_timeout: float = 10):
    """Tap 'ACTIVATE NOW', dismiss system dialogs, then verify success."""
    screen_lock_pin = page_args.get("device_setup", {}).get("screen_lock_pin", "0000")

    SecurityPage(driver, **page_args).activate_now()

    # Android may redirect to com.android.settings asking to re-enter the screen
    # lock PIN to authorize biometric activation.
    if wait_present(driver, _SETTINGS_PIN_ENTRY, timeout=5):
        logger.info("[init_flow] Handling system PIN confirmation for biometric activation")
        driver.find_element(*_SETTINGS_PIN_ENTRY).send_keys(screen_lock_pin)
        driver.press_keycode(_KEYCODE_ENTER)
        WebDriverWait(driver, default_timeout).until(
            lambda d: d.current_package != _SETTINGS_PKG
        )

    # Android may show a system biometric prompt (com.android.systemui) asking to
    # authenticate with fingerprint to confirm linking the biometric feature.
    handle_biometric_if_present(driver, dismiss_timeout=default_timeout)

    if wait_present(driver, _activation_failed_id, timeout=3):
        raise RuntimeError(
            "Heidi biometric activation failed — the emulator/device has no fingerprint enrolled.\n"
            "  Run tests once to auto-enroll, or check device_setup.fingerprint in config.json."
        )


def _onboard(driver, pin: str, page_args: dict, default_timeout: float):
    """Complete the full onboarding flow from the landing screen to home."""
    LandingPage(driver, **page_args).get_started()
    WebDriverWait(driver, default_timeout).until(EC.presence_of_element_located(_privacy_id))
    PrivacyPage(driver, **page_args).continue_onboarding()
    WebDriverWait(driver, default_timeout).until(EC.presence_of_element_located(_security_id))
    _activate_biometric(driver, page_args, default_timeout)
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

    if state == "biometric_unlock":
        logger.info("[init_flow] Biometric unlock prompt — simulating fingerprint")
        handle_biometric_if_present(driver, dismiss_timeout=default_timeout)
        # Wait for the home screen to render after biometric unlock.
        # Use a generous timeout — the app can take a few seconds to transition.
        if wait_present(driver, _home_id, timeout=default_timeout):
            if skip_if_done:
                logger.info("[init_flow] Unlocked — already on home screen")
                return
        state = _detect_state(driver)
        if state == "unknown":
            logger.info("[init_flow] Unknown state after biometric — recovering")
            state = _back_to_known_state(driver, package)
        # Fall through to handle whatever state follows unlock.

    if state == "landing":
        logger.info("[init_flow] Fresh app state — running onboarding")
        _onboard(driver, pin, page_args, default_timeout)

    elif state == "privacy":
        logger.info("[init_flow] Resuming onboarding from privacy screen")
        PrivacyPage(driver, **page_args).continue_onboarding()
        WebDriverWait(driver, default_timeout).until(EC.presence_of_element_located(_security_id))
        _activate_biometric(driver, page_args, default_timeout)
        WebDriverWait(driver, default_timeout).until(EC.presence_of_element_located(_home_id))
        logger.info("[init_flow] Onboarding complete — home screen reached")

    elif state == "security":
        logger.info("[init_flow] Resuming onboarding from security screen")
        _activate_biometric(driver, page_args, default_timeout)
        WebDriverWait(driver, default_timeout).until(EC.presence_of_element_located(_home_id))
        logger.info("[init_flow] Onboarding complete — home screen reached")

    elif state == "home":
        if skip_if_done:
            logger.info("[init_flow] Already on home screen — skipping")
            return
        # skip_if_done=False: wipe app data and re-onboard from scratch
        logger.info(f"[init_flow] Re-onboarding — clearing {package}")
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

    else:
        raise RuntimeError(
            f"[init_flow] App in unrecognized state '{state}' after recovery — "
            "check for unexpected system dialogs or crashed screens."
        )
