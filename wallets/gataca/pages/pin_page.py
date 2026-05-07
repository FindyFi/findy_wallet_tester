from appium.webdriver.common.appiumby import AppiumBy
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

# Gataca uses biometric (fingerprint) as its primary unlock method.
# This heading appears on the app-internal biometric prompt screen.
HEADING = (AppiumBy.XPATH, '//*[@text="Perform biometric verification"]')


def on_screen(driver, timeout=2) -> bool:
    """Return True if the Gataca biometric unlock screen is currently visible."""
    try:
        WebDriverWait(driver, timeout).until(EC.presence_of_element_located(HEADING))
        return True
    except TimeoutException:
        return False
