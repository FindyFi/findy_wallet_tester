from appium.webdriver.common.appiumby import AppiumBy
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

from base.base_page import BasePage

SCREEN_ID = (AppiumBy.XPATH, '//*[@resource-id="ProofRequestAcceptProcessScreen"]')
_success = (AppiumBy.XPATH, '//*[@resource-id="ProofRequestAcceptProcessScreen.animation.success"]')


class ProofRequestAcceptProcessPage(BasePage):
    def wait_for_result(self):
        """Wait for proof request sharing to complete.

        Success path: success animation appears, then screen auto-closes after ~5s.
        Error path: success animation never appears — raises RuntimeError.
        """
        t = self._get_timeout("credential_offer")
        self.find(SCREEN_ID, timeout=t)

        try:
            WebDriverWait(self.driver, t).until(EC.presence_of_element_located(_success))
        except TimeoutException:
            raise RuntimeError(
                "Verification sharing did not complete — success animation never appeared"
            )

        # Screen auto-closes after ~5s; wait for it to disappear
        try:
            WebDriverWait(self.driver, self._get_timeout("credential_offer")).until(EC.invisibility_of_element_located(SCREEN_ID))
        except TimeoutException:
            pass  # already gone or took longer — not a failure
