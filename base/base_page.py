import logging
from selenium.webdriver.support.ui import WebDriverWait
from selenium.common.exceptions import StaleElementReferenceException, TimeoutException
from selenium.webdriver.support import expected_conditions as EC

logger = logging.getLogger(__name__)


class BasePage:
    def __init__(self, driver, debug=False, timeouts=None, **_config):
        # Pages are constructed with the shared `page_args` bag (debug, timeouts, device_pin, ...).
        # Only debug/timeouts are used here; **_config absorbs the rest (e.g. device_pin, which is
        # consumed by the flows, not the pages) so adding a key to page_args never breaks a page.
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

    def click(self, locator, timeout=None, attempts=3):
        """Wait for the element to be clickable, then tap it — retrying if it goes stale.

        `element_to_be_clickable` re-finds the element on every poll, but it can still be replaced
        in the moment between the wait handing it back and the tap landing. That is routine on
        Compose screens that are still animating (a bottom sheet sliding in, a carousel
        recomposing after a tab switch), and it surfaced as `stale element reference` aborting
        authbound's first cleanup run on 2026-08-17. Re-locate and try again rather than failing:
        a stale reference means the screen moved, not that the element is gone.
        """
        t = timeout if timeout is not None else self._get_timeout("default")
        for attempt in range(1, attempts + 1):
            try:
                WebDriverWait(self.driver, t).until(
                    EC.element_to_be_clickable(locator)
                ).click()
                return
            except TimeoutException:
                raise Exception(f"Element {locator} not clickable after {t}s")
            except StaleElementReferenceException:
                logger.info(
                    f"[page] {locator} went stale before the tap "
                    f"(attempt {attempt}/{attempts}) — re-locating"
                )

        raise Exception(
            f"Element {locator} kept going stale — gave up after {attempts} attempts"
        )

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
