from appium.webdriver.common.appiumby import AppiumBy
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

from base.base_page import BasePage

# "My credentials" heading is always visible on the home screen.
SCREEN_ID = (AppiumBy.XPATH, '//*[@text="My credentials"]')

# Credential cards on the home credentials list are rendered as ViewGroups whose
# content-desc has the form "<Type>, <field1>, <field2>, ..." — at least one comma.
# This excludes the DID alias Button (content-desc is the alias name only) and the
# bottom-nav tabs ("Credentials", "Connections", "Settings") which have no commas.
_credential_card = (AppiumBy.XPATH,
    '//android.view.ViewGroup[@content-desc and contains(@content-desc, ", ")]'
)


class HomePage(BasePage):
    def wait_until_loaded(self):
        try:
            WebDriverWait(self.driver, self._get_timeout("default")).until(
                EC.presence_of_element_located(SCREEN_ID)
            )
        except TimeoutException:
            raise RuntimeError("Gataca home screen did not load within timeout")

    def count_credentials(self) -> int:
        """Return the number of credentials currently in the wallet."""
        try:
            return len(self.driver.find_elements(*_credential_card))
        except Exception:
            return 0
