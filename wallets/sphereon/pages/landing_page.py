"""Sphereon's first-run welcome screen.

Captured live 2026-09-10 on 0.9.0 (build 901) from a freshly cleared install.

    Welcome to your Sphereon Wallet
    Receive and share accreditations, credentials, badges and identity details.
    [Get started]                       (plus a language flag button, top right)

Nothing on this screen carries a resource-id — the wallet is React Native and exposes none on any
screen — so the heading is matched on its own copy and the button on its content-desc.
"""
from appium.webdriver.common.appiumby import AppiumBy

from base.base_page import BasePage
from base.utils import wait_present

SCREEN_ID = (AppiumBy.XPATH, '//*[@text="Welcome to your Sphereon Wallet"]')

_cta = (AppiumBy.XPATH, '//*[@content-desc="Get started"]')


def on_screen(driver, timeout: float = 2) -> bool:
    return wait_present(driver, SCREEN_ID, timeout=timeout)


class LandingPage(BasePage):
    def get_started(self):
        self.click(_cta)
