"""One-time wallet setup flow for Gataca.

Ensures the configured DID method is active before credential tests run. The method is chosen by
the wallet config's "did_method" key (see DID_METHODS); the default is "jwk". Call once per pytest
session from the wallet conftest.

Full navigation path from home:
    Home → Settings tab → Personal Information → (current DID shown here)

If the current DID already matches the configured method's prefix, it is active — no action needed.

If an existing alias for that method exists, activate it by:
    Home → tap DID alias button → My identities DIDs → tap the method's alias →
    PIN → DID switches (row expands to show its did prefix)

Otherwise, enable the nested toggles and create a new profile:
    Personal Information → Advanced row → toggle Advanced ON →
      tap Multi DID link → toggle Multi DID ON →
      back to Personal Information → tap "+" button →
      Create Profile dialog → DID Method = <option> → Create →
      authenticate via Android system biometric prompt (PIN) →
      back on Personal Information, active DID now matches the method's prefix.
"""
import logging
import time

from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

from base.android import authenticate_with_pin
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
    find_alias,
    is_alias_active,
    list_aliases,
)

logger = logging.getLogger(__name__)

# Supported DID methods, keyed by the wallet config's "did_method" value. Each spec maps to the
# wallet's UI specifics for that method:
#   prefix — the active-DID string prefix shown on Personal Information / the selector row
#   option — the DID Method dropdown option (content-desc) in the Create Profile dialog
#   alias  — the identity alias label the wallet auto-assigns (used to find/select an existing one)
DID_METHODS = {
    "gatc": {"prefix": "did:gatc:", "option": "Gataca",       "alias": "Gataca"},
    "jwk":  {"prefix": "did:jwk:",  "option": "JWK",          "alias": "JWK Identity"},
    "ebsi": {"prefix": "did:key:",  "option": "Ebsi Subject", "alias": "Ebsi Identity"},
}

DEFAULT_DID_METHOD = "jwk"


def _device_pin(page_args: dict) -> str:
    """The device PIN used to answer system biometric prompts (from the wallet's android config)."""
    return page_args.get("device_pin", "")


def ensure_did(driver, app_package: str, did_method: str = DEFAULT_DID_METHOD, **page_args) -> bool:
    """Navigate to Personal Information and ensure the configured DID method is active.

    Must be called from the home screen.

    Args:
        did_method: a key of DID_METHODS (e.g. "jwk", "gatc", "ebsi"); defaults to "jwk".

    Returns:
        True  — the method was already the active DID; no changes made.
        False — an existing alias was activated, or a new profile was created and is now active.

    Raises:
        ValueError   — if did_method is not a supported DID method.
        RuntimeError — if the Settings or Personal Information screen cannot be reached,
                       or the biometric prompt does not dismiss.
    """
    spec = DID_METHODS.get(did_method)
    if spec is None:
        raise ValueError(
            f"[setup_flow] Unsupported did_method '{did_method}'. "
            f"Supported: {', '.join(sorted(DID_METHODS))}"
        )
    prefix, alias = spec["prefix"], spec["alias"]

    timeouts = page_args.get("timeouts", {})
    default_timeout = timeouts.get("default", 10)

    logger.info(f"[setup_flow] Ensuring DID method '{did_method}' ({prefix}) is active")

    _open_personal_information(driver, default_timeout, page_args)

    personal_info = PersonalInfoPage(driver, **page_args)
    current_did = personal_info.current_did()
    logger.info(f"[setup_flow] Current DID: {current_did[:50]}...")

    if personal_info.is_method_active(prefix):
        logger.info(f"[setup_flow] {did_method} ({prefix}) is already active — no changes needed")
        _return_home(driver, default_timeout)
        return True

    logger.info(f"[setup_flow] {did_method} not active — looking for existing '{alias}' alias")
    _return_home(driver, default_timeout)

    if _activate_existing_alias(driver, default_timeout, page_args, spec):
        logger.info(f"[setup_flow] {did_method} DID is now active (existing alias)")
        return False

    logger.info(f"[setup_flow] No existing '{alias}' alias — enabling Multi DID and creating profile")

    _open_personal_information(driver, default_timeout, page_args)
    _enable_advanced_and_multi_did(driver, default_timeout, page_args)

    # We should now be back on Personal Information; re-wait for it.
    WebDriverWait(driver, default_timeout).until(
        EC.presence_of_element_located(_personal_info_id)
    )

    _create_profile(driver, default_timeout, page_args, spec)

    # The active DID may take a moment to update in the UI after profile creation.
    personal_info = PersonalInfoPage(driver, **page_args)
    deadline = time.time() + default_timeout
    now_active = False
    while time.time() < deadline:
        if personal_info.is_method_active(prefix):
            now_active = True
            break
        time.sleep(1)

    if not now_active:
        new_did = personal_info.current_did()
        raise RuntimeError(
            f"[setup_flow] Created {did_method} profile but active DID is not {prefix} "
            f"(got: {new_did[:50]}...)"
        )

    logger.info(f"[setup_flow] {did_method} DID is now active")
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


def _activate_existing_alias(driver, default_timeout: float, page_args: dict, spec: dict) -> bool:
    """From home, open the DID selector and activate an existing alias for `spec` if one exists.

    Returns True if the alias was found and activated, False otherwise.
    Leaves the user on home when done.
    """
    alias, prefix = spec["alias"], spec["prefix"]
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

    # Enumerate the DIDs that already exist so we only create one when the configured method
    # is genuinely missing (avoids piling up duplicate profiles across runs).
    logger.info(f"[setup_flow] Existing identities: {list_aliases(driver)}")

    alias_el = find_alias(driver, alias)
    if alias_el is None:
        logger.info(f"[setup_flow] No existing '{alias}' alias found in selector — will create one")
        driver.back()
        return False

    alias_name = alias_el.get_attribute("content-desc") or alias
    logger.info(f"[setup_flow] Found existing alias: {alias_name} — activating")
    alias_el.click()

    # Biometric prompt appears to confirm the DID switch — authenticate via PIN.
    time.sleep(2)
    authenticate_with_pin(driver, _device_pin(page_args))

    # The selected row expands to include its did prefix once the switch registers.
    deadline = time.time() + default_timeout
    while time.time() < deadline:
        if is_alias_active(driver, alias, prefix, timeout=1):
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


def _create_profile(driver, default_timeout: float, page_args: dict, spec: dict):
    """From Personal Information, tap "+" and create a profile for the `spec` DID method.

    Handles the biometric prompt that appears after tapping Create (via PIN).
    """
    personal_info = PersonalInfoPage(driver, **page_args)
    personal_info.open_create_profile()

    create_profile = CreateProfilePage(driver, **page_args)
    create_profile.wait_until_loaded()
    create_profile.select_method(spec["option"])
    create_profile.create()

    # System biometric prompt appears. The prompt is rendered by com.android.systemui and
    # takes a moment to surface. Give it time then authenticate via PIN.
    time.sleep(2)
    authenticate_with_pin(driver, _device_pin(page_args))

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

    raise RuntimeError("[setup_flow] Could not return to home after DID setup")
