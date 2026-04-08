import logging
from selenium.webdriver.support.ui import WebDriverWait
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.support import expected_conditions as EC

logger = logging.getLogger(__name__)


class BasePage:
    def __init__(self, driver, debug=False, timeouts=None):
        self.driver = driver
        self.debug = debug
        self.timeouts = timeouts or {}

    def _get_timeout(self, key: str, fallback=10.0):
        """Return timeout value from config, falling back to 'default', then the hard fallback."""
        return self.timeouts.get(key, self.timeouts.get("default", fallback))

    def find(self, locator, timeout=None):
        t = timeout if timeout is not None else self._get_timeout("default")
        try:
            return WebDriverWait(self.driver, t).until(
                EC.presence_of_element_located(locator)
            )
        except TimeoutException:
            raise Exception(f"Element {locator} not found")

    def click(self, locator, timeout=None):
        t = timeout if timeout is not None else self._get_timeout("default")
        try:
            WebDriverWait(self.driver, t).until(
                EC.element_to_be_clickable(locator)
            ).click()
        except TimeoutException:
            raise Exception(f"Element {locator} not clickable after {t}s")

    def swipe_up(self):
        """Swipe upward to reveal content below the fold (75% → 25% of screen height)."""
        import time
        from selenium.webdriver.common.actions.action_builder import ActionBuilder
        from selenium.webdriver.common.actions.pointer_input import PointerInput
        size = self.driver.get_window_size()
        cx = size["width"] // 2
        touch = PointerInput("touch", "finger")
        builder = ActionBuilder(self.driver, mouse=touch)
        builder.pointer_action.move_to_location(cx, int(size["height"] * 0.75))
        builder.pointer_action.pointer_down()
        builder.pointer_action.pause(0.1)
        builder.pointer_action.move_to_location(cx, int(size["height"] * 0.25))
        builder.pointer_action.release()
        builder.perform()
        time.sleep(0.3)
