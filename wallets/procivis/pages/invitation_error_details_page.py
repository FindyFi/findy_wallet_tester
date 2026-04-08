from appium.webdriver.common.appiumby import AppiumBy

from base.base_page import BasePage

SCREEN_ID = (AppiumBy.XPATH, '//*[@resource-id="InvitationErrorDetailsScreen"]')
_close = (AppiumBy.XPATH, '//*[@resource-id="InvitationErrorDetailsScreen.closeIcon"]')

_attr_labels = '//*[contains(@resource-id, ".attributeLabel") and @text!=""]'
_attr_values = '//*[contains(@resource-id, ".attributeValue") and @text!=""]'


class InvitationErrorDetailsPage(BasePage):
    def collect_details(self) -> dict:
        """Return all visible label→value pairs from the error details screen."""
        self.find((AppiumBy.XPATH, _attr_labels))  # wait for content, not just the container
        labels = self.driver.find_elements(AppiumBy.XPATH, _attr_labels)
        values = self.driver.find_elements(AppiumBy.XPATH, _attr_values)
        return {
            lbl.get_attribute("text"): val.get_attribute("text")
            for lbl, val in zip(labels, values)
        }
