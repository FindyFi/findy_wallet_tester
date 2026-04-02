from appium.webdriver.common.appiumby import AppiumBy
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

from base.base_page import BasePage
from base.utils import wait_present
from wallets.paradym.flows import check_for_error

_accept = (AppiumBy.XPATH, '//*[@content-desc="Accept"]')
_fetching = (AppiumBy.XPATH, '//*[@text="Fetching information"]')


class CredentialOfferPage(BasePage):
    def wait_until_loaded(self, timeout=None):
        # Use a dedicated credential_offer timeout — fetching from the issuer
        # can take 10-20s before the Accept/Decline screen appears.
        t = timeout if timeout is not None else self._get_timeout("credential_offer", fallback=30)
        try:
            WebDriverWait(self.driver, t).until(
                EC.presence_of_element_located(_accept)
            )
        except TimeoutException:
            check_for_error(self.driver, "credential offer screen")
            if wait_present(self.driver, _fetching, timeout=1):
                raise RuntimeError(
                    f"Credential offer timed out after {t}s still fetching — "
                    "the offer URL may have expired, or increase 'credential_offer' timeout in config"
                )
            raise RuntimeError(
                "Credential offer screen did not appear — "
                "check that the deeplink was handled by the app"
            )

    def accept(self):
        self.wait_until_loaded()
        self.click(_accept)
