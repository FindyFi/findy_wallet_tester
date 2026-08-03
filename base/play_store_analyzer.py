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
    UPDATE_AVAILABLE = "update_available"
    DOWNLOADING = "downloading"
    INSTALLING = "installing"
    INSTALLED = "installed"
    ERROR = "error"
    POPUP = "popup"


# Text on the app details page's primary action button, mapped to the state it implies.
# Only the *topmost* match counts: the related-apps section further down the page carries
# its own Install/Update/Open buttons, and matching one of those reports another app's state.
ACTION_TEXTS = {
    "Install": PlayStoreState.READY_TO_INSTALL,
    "Asenna": PlayStoreState.READY_TO_INSTALL,
    "Update": PlayStoreState.UPDATE_AVAILABLE,
    "Päivitä": PlayStoreState.UPDATE_AVAILABLE,
    "Open": PlayStoreState.INSTALLED,
    "Avaa": PlayStoreState.INSTALLED,
}

DISMISS_TEXTS = [
    "Skip", "Not now", "No thanks", "Accept", "Got it", "Continue", "Dismiss",
    # Finnish
    "Ohita", "Ei nyt", "Ei kiitos", "Hyväksy", "Selvä", "Jatka", "Hylkää",
]

ERROR_TEXTS = {
    "Not enough storage space": "Device is out of storage. Free up space and try again.",
    "Not enough space": "Device is out of storage. Free up space and try again.",
    "Insufficient storage": "Device is out of storage. Free up space and try again.",
    "Download failed": "Play Store download failed. Check network connection.",
    "Error downloading": "Play Store download failed. Check network connection.",
    "No connection": "No network connection. Check device connectivity.",
    "Can't download": "Play Store could not download the app.",
    "Item not found": "App not found on Play Store. Check the package name.",
    # Finnish
    "Tallennustilaa ei ole": "Device is out of storage. Free up space and try again.",
    "Lataus epäonnistui": "Play Store download failed. Check network connection.",
    "Ei yhteyttä": "No network connection. Check device connectivity.",
    "Kohdetta ei löydy": "App not found on Play Store. Check the package name.",
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

    @abstractmethod
    def find_primary_action(self, driver):
        """Return (element, state) for the app's own action button, or (None, None).

        The element is what a caller should click to act on the app the details page is
        showing — never a button belonging to a related app listed further down.
        """
        pass


class KeywordPlayStoreAnalyzer(PlayStoreAnalyzer):
    """XPath-based Play Store analyzer.

    Detection priority: ERROR > POPUP > primary action button > INSTALLING >
    DOWNLOADING > UNKNOWN

    The three mutually exclusive "what can I do with this app" states —
    UPDATE_AVAILABLE, INSTALLED and READY_TO_INSTALL — are decided together, by the
    text of the *topmost* action button (see ``find_primary_action``). Two reasons:

    * When an update is pending the details page shows "Update" *next to* "Open", so
      testing "Open" first would report INSTALLED and mask the available update.
    * Any of the three texts can also appear on a related app's card lower down the
      page; deciding on position rather than on match order ignores those.
    """

    _ACTION_XPATH = '//*[' + " or ".join(f'@text="{t}"' for t in ACTION_TEXTS) + ']'

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

        _, action_state = self.find_primary_action(driver)
        if action_state is not None:
            return action_state

        if self._exists(driver, '//*[contains(@text, "Installing") or contains(@text, "Asennetaan")]'):
            return PlayStoreState.INSTALLING

        try:
            pb = driver.find_element(AppiumBy.CLASS_NAME, "android.widget.ProgressBar")
            if int(pb.get_attribute("max") or 0) > 0:
                return PlayStoreState.DOWNLOADING
        except Exception:
            pass

        return PlayStoreState.UNKNOWN

    def find_primary_action(self, driver):
        """Return (element, state) for the topmost Install/Update/Open button.

        Position, not match order, decides which button is the app's own: the details page
        for the requested app puts its action button at the top, while the related-apps
        section lower down carries buttons for entirely different packages.
        """
        try:
            elements = driver.find_elements(AppiumBy.XPATH, self._ACTION_XPATH)
            if not elements:
                return None, None
            topmost = min(elements, key=lambda el: el.location["y"])
            return topmost, ACTION_TEXTS.get(topmost.get_attribute("text") or "")
        except Exception:
            # Elements can go stale mid-read while the page re-renders.
            return None, None

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
