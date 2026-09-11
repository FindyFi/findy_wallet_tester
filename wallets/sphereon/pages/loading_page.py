"""Sphereon's "working on it" screen, the wallet saying a deeplink is still being processed.

Captured live 2026-09-10 on 0.9.0 (build 901) by polling every 0.7s after firing an issuance
deeplink: home is still showing at 1.0s, this appears at 1.9s, and the trust consent screen
replaces it at 4.3s.

    Getting information...

Matched with `starts-with`, because the ellipsis has been seen as three ASCII dots and the wallet
may well switch to the single character. Only this one wording has been observed, so a different
step's spinner would currently read as no screen at all.
"""
from appium.webdriver.common.appiumby import AppiumBy

from base.utils import wait_present

SCREEN_ID = (AppiumBy.XPATH, '//*[starts-with(@text,"Getting information")]')


def on_screen(driver, timeout: float = 1) -> bool:
    return wait_present(driver, SCREEN_ID, timeout=timeout)
