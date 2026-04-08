import logging

from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

from wallets.heidi.pages.home_page import HomePage, SCREEN_ID as _home_id
from wallets.heidi.pages.settings_page import (
    SettingsPage,
    SCREEN_ID as _settings_id,
    CONNECTIONS_SCREEN_ID as _connections_id,
)

logger = logging.getLogger(__name__)


def configure(driver, **page_args):
    """From the home screen: enable Show Metadata and ensure Always Ask is set for Connections.

    Must be called while the home screen is visible. Returns to the home screen when done.
    """
    timeouts = page_args.get("timeouts", {})
    default_timeout = timeouts.get("default", 10)

    home = HomePage(driver, **page_args)
    settings = SettingsPage(driver, **page_args)

    logger.info("[settings_flow] Opening Settings")
    home.open_settings()
    WebDriverWait(driver, default_timeout).until(EC.presence_of_element_located(_settings_id))

    if settings.is_show_metadata_enabled():
        logger.info("[settings_flow] Show Metadata already enabled")
    else:
        logger.info("[settings_flow] Enabling Show Metadata")
        settings.enable_show_metadata()

    logger.info("[settings_flow] Opening Connections")
    settings.open_connections()
    WebDriverWait(driver, default_timeout).until(EC.presence_of_element_located(_connections_id))

    logger.info("[settings_flow] Setting Always Ask for trusted issuer/verifier")
    settings.select_always_ask_trusted()
    logger.info("[settings_flow] Setting Always Ask for untrusted issuer/verifier")
    settings.select_always_ask_untrusted()

    logger.info("[settings_flow] Going back to Settings")
    driver.back()
    WebDriverWait(driver, default_timeout).until(EC.presence_of_element_located(_settings_id))

    logger.info("[settings_flow] Going back to Home")
    driver.back()
    WebDriverWait(driver, default_timeout).until(EC.presence_of_element_located(_home_id))
