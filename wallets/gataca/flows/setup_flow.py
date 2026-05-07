"""One-time wallet setup flow for Gataca.

Ensures an Ebsi Subject DID (rendered as did:key:...) is active before credential tests run.
Call once per pytest session from the wallet conftest.

Full navigation path from home:
    Home → Settings tab → Personal Information → (current DID shown here)

If the current DID is already did:key:..., EBSI is active — no action needed.

If an existing Ebsi Identity alias exists, activate it by:
    Home → tap DID alias button → My identities DIDs → tap Ebsi alias →
    biometric → DID switches

Otherwise, enable the nested toggles and create a new EBSI profile:
    Personal Information → Advanced row → toggle Advanced ON →
      tap Multi DID link → toggle Multi DID ON →
      back to Personal Information → tap "+" button →
      Create Profile dialog → DID Method = Ebsi Subject → Create →
      authenticate via Android system fingerprint prompt →
      back on Personal Information, active DID is now did:key:...
"""
import logging
import time

from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

from appium.webdriver.common.appiumby import AppiumBy

from base.android import handle_biometric_if_present
from base.utils import wait_present
from wallets.gataca.pages.home_page import SCREEN_ID as _home_id
from wallets.gataca.pages.settings_page import SettingsPage, SETTINGS_TAB
from wallets.gataca.pages.personal_info_page import PersonalInfoPage, SCREEN_ID as _personal_info_id
from wallets.gataca.pages.advanced_page import AdvancedPage
from wallets.gataca.pages.multi_did_page import MultiDidPage
from wallets.gataca.pages.create_profile_page import CreateProfilePage
from wallets.gataca.pages.did_selector_page import (
    SCREEN_ID as _selector_id,
    HOME_DID_ALIAS_BTN,
    find_ebsi_alias,
)

logger = logging.getLogger(__name__)


def ensure_ebsi_did(driver, app_package: str, **page_args) -> bool:
    """Navigate to Personal Information and ensure the active DID is an Ebsi Subject (did:key:).

    Must be called from the home screen.

    Returns:
        True  — EBSI was already the active DID; no changes made.
        False — An EBSI profile was activated or created and is now active.

    Raises:
        RuntimeError — if the Settings or Personal Information screen cannot be reached,
                       or the biometric prompt does not dismiss.
    """
    timeouts = page_args.get("timeouts", {})
    default_timeout = timeouts.get("default", 10)

    logger.info("[setup_flow] Checking active DID method")

    _open_personal_information(driver, default_timeout, page_args)

    personal_info = PersonalInfoPage(driver, **page_args)
    current_did = personal_info.current_did()
    logger.info(f"[setup_flow] Current DID: {current_did[:50]}...")

    if personal_info.is_ebsi_active():
        logger.info("[setup_flow] EBSI (did:key) is already active — no changes needed")
        _return_home(driver, default_timeout)
        return True

    logger.info("[setup_flow] EBSI not active — looking for existing EBSI alias")
    _return_home(driver, default_timeout)

    if _activate_existing_ebsi_alias(driver, default_timeout, page_args):
        logger.info("[setup_flow] EBSI DID is now active (existing alias)")
        return False

    logger.info("[setup_flow] No existing EBSI alias — enabling Multi DID and creating profile")

    _open_personal_information(driver, default_timeout, page_args)
    _enable_advanced_and_multi_did(driver, default_timeout, page_args)

    # We should now be back on Personal Information; re-wait for it.
    WebDriverWait(driver, default_timeout).until(
        EC.presence_of_element_located(_personal_info_id)
    )

    _create_ebsi_profile(driver, default_timeout, page_args)

    # The active DID may take a moment to update in the UI after profile creation.
    deadline = time.time() + default_timeout
    personal_info = PersonalInfoPage(driver, **page_args)
    while time.time() < deadline:
        if personal_info.is_ebsi_active():
            break
        time.sleep(1)
    else:
        new_did = personal_info.current_did()
        raise RuntimeError(
            f"[setup_flow] Created Ebsi Subject profile but active DID is not did:key: (got: {new_did[:50]}...)"
        )

    logger.info("[setup_flow] EBSI DID is now active")
    _return_home(driver, default_timeout)
    return False


def _open_personal_information(driver, default_timeout: float, page_args: dict):
    """Navigate from home to the Personal Information screen."""
    try:
        WebDriverWait(driver, default_timeout).until(
            EC.element_to_be_clickable(SETTINGS_TAB)
        ).click()
    except TimeoutException:
        raise RuntimeError("[setup_flow] Could not find Settings tab on home screen")

    settings_page = SettingsPage(driver, **page_args)
    settings_page.wait_until_loaded()
    settings_page.open_personal_information()

    personal_info = PersonalInfoPage(driver, **page_args)
    personal_info.wait_until_loaded()


def _activate_existing_ebsi_alias(driver, default_timeout: float, page_args: dict) -> bool:
    """From home, open the DID selector and activate an existing EBSI alias if one exists.

    Returns True if an EBSI alias was found and activated, False otherwise.
    Leaves the user on home when done.
    """
    try:
        WebDriverWait(driver, default_timeout).until(
            EC.element_to_be_clickable(HOME_DID_ALIAS_BTN)
        ).click()
    except TimeoutException:
        logger.warning("[setup_flow] Could not tap DID alias button on home")
        return False

    try:
        WebDriverWait(driver, default_timeout).until(
            EC.presence_of_element_located(_selector_id)
        )
    except TimeoutException:
        logger.warning("[setup_flow] My identities DIDs screen did not open")
        driver.back()
        return False

    ebsi_el = find_ebsi_alias(driver)
    if ebsi_el is None:
        logger.info("[setup_flow] No existing EBSI alias found in selector")
        driver.back()
        return False

    alias_name = ebsi_el.get_attribute("content-desc") or "Ebsi alias"
    logger.info(f"[setup_flow] Found existing EBSI alias: {alias_name} — activating")
    ebsi_el.click()

    # Biometric prompt appears to confirm the DID switch.
    time.sleep(2)
    if not handle_biometric_if_present(driver):
        driver.execute_script("mobile: fingerprint", {"fingerprintId": 1})

    _ACTIVATED = (AppiumBy.XPATH, '//*[contains(@text, "is now active")]')
    deadline = time.time() + default_timeout
    while time.time() < deadline:
        if wait_present(driver, _ACTIVATED, timeout=1):
            time.sleep(1)  # brief pause for DID to fully register
            break
        if wait_present(driver, _home_id, timeout=1):
            break
        time.sleep(0.5)

    _return_home(driver, default_timeout)
    return True


def _enable_advanced_and_multi_did(driver, default_timeout: float, page_args: dict):
    """From Personal Information, drill into Advanced → Multi DID and enable both toggles.

    Leaves the user back on the Personal Information screen when done.
    """
    personal_info = PersonalInfoPage(driver, **page_args)
    personal_info.open_advanced()

    advanced = AdvancedPage(driver, **page_args)
    advanced.wait_until_loaded()

    # Turn on Advanced if the Multi DID link isn't already showing.
    if not advanced.has_multi_did_link():
        logger.info("[setup_flow] Turning on Advanced toggle")
        advanced.toggle_advanced()
        try:
            WebDriverWait(driver, default_timeout).until(
                lambda d: advanced.has_multi_did_link()
            )
        except TimeoutException:
            raise RuntimeError("[setup_flow] Multi DID link did not appear after enabling Advanced")

    advanced.open_multi_did()

    multi_did = MultiDidPage(driver, **page_args)
    multi_did.wait_until_loaded()

    if not multi_did.is_enabled():
        logger.info("[setup_flow] Turning on Multi DID toggle")
        multi_did.toggle_multi_did()
        try:
            WebDriverWait(driver, default_timeout).until(
                lambda d: multi_did.is_enabled()
            )
        except TimeoutException:
            raise RuntimeError("[setup_flow] Multi DID feature did not enable")

    # Navigate back to Personal Information: Multi DID → Advanced → Personal Information.
    driver.back()
    driver.back()


def _create_ebsi_profile(driver, default_timeout: float, page_args: dict):
    """From Personal Information, tap "+" and create an Ebsi Subject profile.

    Handles the biometric prompt that appears after tapping Create.
    """
    personal_info = PersonalInfoPage(driver, **page_args)
    personal_info.open_create_profile()

    create_profile = CreateProfilePage(driver, **page_args)
    create_profile.wait_until_loaded()
    create_profile.select_ebsi_subject()
    create_profile.create()

    # System biometric prompt appears. The prompt is rendered by com.android.systemui and
    # takes a moment to surface. Give it time then send the fingerprint.
    time.sleep(2)
    if not handle_biometric_if_present(driver):
        # Fallback: send fingerprint unconditionally (the prompt may have a secure-flag
        # variant that UiAutomator2 can't detect).
        logger.info("[setup_flow] Biometric prompt not detected via handler — sending fingerprint anyway")
        driver.execute_script("mobile: fingerprint", {"fingerprintId": 1})

    # Wait for return to Personal Information.
    try:
        WebDriverWait(driver, default_timeout * 2).until(
            EC.presence_of_element_located(_personal_info_id)
        )
    except TimeoutException:
        raise RuntimeError("[setup_flow] Did not return to Personal Information after creating profile")


def _return_home(driver, default_timeout: float):
    """Back out of Settings all the way to the home screen (My credentials)."""
    for _ in range(4):
        try:
            WebDriverWait(driver, 1).until(
                EC.presence_of_element_located(_home_id)
            )
            return
        except TimeoutException:
            driver.back()

    raise RuntimeError("[setup_flow] Could not return to home after EBSI setup")
