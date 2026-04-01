import logging
from abc import ABC, abstractmethod
from enum import Enum
from appium.webdriver.common.appiumby import AppiumBy
from selenium.webdriver.support.ui import WebDriverWait
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.support import expected_conditions as EC

logger = logging.getLogger(__name__)


class PlayStoreState(Enum):
    UNKNOWN = "unknown"
    READY_TO_INSTALL = "ready_to_install"
    DOWNLOADING = "downloading"
    INSTALLING = "installing"
    INSTALLED = "installed"
    ERROR = "error"
    POPUP = "popup"


DISMISS_TEXTS = ["Skip", "Not now", "No thanks", "Accept", "Got it", "Continue", "Dismiss"]

ERROR_TEXTS = {
    "Not enough storage space": "Device is out of storage. Free up space and try again.",
    "Not enough space": "Device is out of storage. Free up space and try again.",
    "Insufficient storage": "Device is out of storage. Free up space and try again.",
    "Download failed": "Play Store download failed. Check network connection.",
    "Error downloading": "Play Store download failed. Check network connection.",
    "No connection": "No network connection. Check device connectivity.",
    "Can't download": "Play Store could not download the app.",
    "Item not found": "App not found on Play Store. Check the package name.",
}


class PlayStoreAnalyzer(ABC):
    """Abstract interface for Play Store screen analysis.
    Subclass this to swap in a different detection strategy (e.g. ML-based vision model).
    """

    @abstractmethod
    def get_state(self, driver) -> PlayStoreState:
        """Read the current screen and return the detected Play Store state."""
        pass

    @abstractmethod
    def get_error_description(self, driver) -> str:
        """Return a human-readable error description. Call only when state is ERROR."""
        pass

    @abstractmethod
    def dismiss_popup(self, driver) -> bool:
        """Dismiss a visible popup. Returns True if something was clicked."""
        pass


class KeywordPlayStoreAnalyzer(PlayStoreAnalyzer):
    """XPath-based Play Store analyzer.
    Detection priority: ERROR > POPUP > INSTALLED > INSTALLING > DOWNLOADING > READY_TO_INSTALL > UNKNOWN
    """

    def _exists(self, driver, xpath, timeout=0.5) -> bool:
        try:
            WebDriverWait(driver, timeout).until(
                EC.presence_of_element_located((AppiumBy.XPATH, xpath))
            )
            return True
        except TimeoutException:
            return False

    def get_state(self, driver) -> PlayStoreState:
        for error_text in ERROR_TEXTS:
            if self._exists(driver, f'//*[contains(@text, "{error_text}")]'):
                return PlayStoreState.ERROR

        dismiss_xpath = " or ".join(f'@text="{t}"' for t in DISMISS_TEXTS)
        if self._exists(driver, f'//*[{dismiss_xpath}]'):
            return PlayStoreState.POPUP

        if self._exists(driver, '//*[@text="Open"]'):
            return PlayStoreState.INSTALLED

        if self._exists(driver, '//*[contains(@text, "Installing")]'):
            return PlayStoreState.INSTALLING

        try:
            pb = driver.find_element(AppiumBy.CLASS_NAME, "android.widget.ProgressBar")
            if int(pb.get_attribute("max") or 0) > 0:
                return PlayStoreState.DOWNLOADING
        except Exception:
            pass

        if self._exists(driver, '//*[@text="Install"]'):
            return PlayStoreState.READY_TO_INSTALL

        return PlayStoreState.UNKNOWN

    def get_error_description(self, driver) -> str:
        for error_text, description in ERROR_TEXTS.items():
            try:
                el = WebDriverWait(driver, 0.5).until(
                    EC.presence_of_element_located(
                        (AppiumBy.XPATH, f'//*[contains(@text, "{error_text}")]')
                    )
                )
                screen_text = el.get_attribute("text") or error_text
                return f"{description} (screen: '{screen_text}')"
            except TimeoutException:
                continue
        return "Unknown error on Play Store."

    def dismiss_popup(self, driver) -> bool:
        dismiss_xpath = " or ".join(f'@text="{t}"' for t in DISMISS_TEXTS)
        try:
            el = WebDriverWait(driver, 0.5).until(
                EC.presence_of_element_located((AppiumBy.XPATH, f'//*[{dismiss_xpath}]'))
            )
            logger.info(f"[popup] Dismissed: '{el.get_attribute('text') or ''}'")
            el.click()
            return True
        except TimeoutException:
            return False
