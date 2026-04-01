import logging
import time

from appium.webdriver.common.appiumby import AppiumBy
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, WebDriverException

from base.base_page import BasePage
from base.play_store_analyzer import KeywordPlayStoreAnalyzer, PlayStoreState

logger = logging.getLogger(__name__)


class BaseTest:
    def __init__(self, driver, config):
        self.driver = driver
        self.config = config
        self._analyzer = KeywordPlayStoreAnalyzer()

    @property
    def page_args(self):
        """Keyword args for constructing any page object from this test's config."""
        return {
            "debug": self.config.get("debug", False),
            "timeouts": self.config.get("timeouts", {}),
        }

    def setup(self):
        app_package = self.config["application"]["package"]
        if not self.driver.is_app_installed(app_package):
            self._install_app_from_play_store(app_package)

    def _install_app_from_play_store(self, app_package):
        """Open Play Store and install the app, reacting to each screen state."""
        debug = self.config.get("debug", False)
        play_store_url = f"https://play.google.com/store/apps/details?id={app_package}"
        install_locator = (AppiumBy.XPATH, '//*[@text="Install"]')

        logger.info(f"[install] Opening Play Store for: {app_package}")
        self.driver.execute_script("mobile: deepLink", {"url": play_store_url, "package": "com.android.vending"})
        self._wait_for_state(PlayStoreState.READY_TO_INSTALL, timeout=20)

        # Click the topmost Install button on the page.
        # The Play Store page may contain Install buttons for related apps below
        # the main app — selecting by smallest y-coordinate ensures we always
        # click the main app's button, not a lower app's.
        logger.info("[install] Clicking Install...")
        WebDriverWait(self.driver, 10).until(
            EC.presence_of_element_located(install_locator)
        )
        candidates = self.driver.find_elements(*install_locator)
        topmost_button = min(candidates, key=lambda el: el.location["y"])
        topmost_button.click()

        timeout = 120
        poll_interval = 2
        elapsed = 0
        prev_state = None

        while elapsed < timeout:
            if self.driver.is_app_installed(app_package):
                logger.info(f"[install] SUCCESS — installed after {elapsed}s.")
                return

            try:
                state = self._analyzer.get_state(self.driver)
            except WebDriverException:
                # UiAutomator2 instrumentation is temporarily killed during installation.
                # This is expected — sleep and let the install finish.
                logger.info(f"[install] UiAutomator2 unavailable (installing) — waiting... ({elapsed}s)")
                time.sleep(poll_interval)
                elapsed += poll_interval
                continue

            if state != prev_state:
                logger.info(f"[state] → {state.value}")
                prev_state = state

            if state == PlayStoreState.ERROR:
                description = self._analyzer.get_error_description(self.driver)
                logger.error(f"[install] STOPPED — {description}")
                raise Exception(f"Play Store error: {description}")

            elif state == PlayStoreState.POPUP:
                if debug:
                    self._log_screen_text()
                self._analyzer.dismiss_popup(self.driver)

            elif state == PlayStoreState.DOWNLOADING:
                try:
                    pb = self.driver.find_element(AppiumBy.CLASS_NAME, "android.widget.ProgressBar")
                    current = int(pb.get_attribute("progress") or 0)
                    maximum = int(pb.get_attribute("max") or 1)
                    logger.info(f"[install] Downloading... {int(current / maximum * 100)}%")
                except Exception:
                    logger.info(f"[install] Downloading... ({elapsed}s elapsed)")

            elif state == PlayStoreState.INSTALLING:
                logger.info(f"[install] Finalising... ({elapsed}s elapsed)")

            elif state == PlayStoreState.UNKNOWN:
                logger.info(f"[install] Unknown state ({elapsed}s elapsed)")
                if debug:
                    self._log_screen_text()

            time.sleep(poll_interval)
            elapsed += poll_interval

        logger.error(f"[install] STOPPED — {app_package} not installed within {timeout}s.")
        raise TimeoutException(f"[install] TIMEOUT: {app_package} not installed within {timeout}s.")

    def _wait_for_state(self, expected: PlayStoreState, timeout=20):
        """Wait until Play Store reaches the expected state. Auto-dismisses popups, raises on ERROR."""
        debug = self.config.get("debug", False)
        deadline = time.time() + timeout
        while time.time() < deadline:
            state = self._analyzer.get_state(self.driver)

            if state == expected:
                logger.info(f"[state] Reached: {state.value}")
                return state

            if state == PlayStoreState.ERROR:
                description = self._analyzer.get_error_description(self.driver)
                logger.error(f"[state] STOPPED — {description}")
                raise Exception(f"Play Store error: {description}")

            if state == PlayStoreState.POPUP:
                if debug:
                    self._log_screen_text()
                self._analyzer.dismiss_popup(self.driver)
                continue

            logger.info(f"[state] Waiting for '{expected.value}', current: '{state.value}'")
            time.sleep(0.5)

        actual = self._analyzer.get_state(self.driver)
        raise TimeoutException(f"Timed out waiting for '{expected.value}', got '{actual.value}'")

    def _log_screen_text(self):
        try:
            elements = self.driver.find_elements(AppiumBy.XPATH, '//*[@text!=""]')
            texts = [el.get_attribute("text") for el in elements if el.get_attribute("text")]
            if texts:
                logger.info(f"[screen] Visible text: {texts}")
        except Exception:
            pass
