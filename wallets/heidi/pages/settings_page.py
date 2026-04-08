from appium.webdriver.common.appiumby import AppiumBy

from base.base_page import BasePage

SCREEN_ID = (AppiumBy.XPATH, '//*[@text="APP SETTINGS"]')

# The "Show Metadata" toggle row — the parent View is the checkable/clickable container.
_SHOW_METADATA_ROW = (AppiumBy.XPATH, '//*[@checkable="true" and .//*[@text="Show Metadata"]]')

# The "Connections" row — its parent View is clickable.
_CONNECTIONS_ROW = (AppiumBy.XPATH, '//*[@clickable="true" and .//*[@text="Connections"]]')

# Connections sub-page.  The page has two independent sections; each has its own "Always Ask" row.
# Selection state is not exposed as an XML attribute, so we always click to ensure correct state.
CONNECTIONS_SCREEN_ID = (AppiumBy.XPATH, '//*[@text="TRUSTED ISSUER/VERIFIER"]')

_TRUSTED_ALWAYS_ASK_ROW = (AppiumBy.XPATH,
    '//android.widget.TextView[@text="TRUSTED ISSUER/VERIFIER"]'
    '/following-sibling::android.view.View[1]'
    '/android.view.View[.//android.widget.TextView[@text="Always Ask"]]'
)
_UNTRUSTED_ALWAYS_ASK_ROW = (AppiumBy.XPATH,
    '//android.widget.TextView[@text="UNTRUSTED ISSUER/VERIFIER"]'
    '/following-sibling::android.view.View[1]'
    '/android.view.View[.//android.widget.TextView[@text="Always Ask"]]'
)


class SettingsPage(BasePage):
    def is_show_metadata_enabled(self) -> bool:
        return self.find(_SHOW_METADATA_ROW).get_attribute("checked") == "true"

    def enable_show_metadata(self):
        if not self.is_show_metadata_enabled():
            self.click(_SHOW_METADATA_ROW)

    def open_connections(self):
        self.click(_CONNECTIONS_ROW)

    def select_always_ask_trusted(self):
        self.click(_TRUSTED_ALWAYS_ASK_ROW)

    def select_always_ask_untrusted(self):
        self.click(_UNTRUSTED_ALWAYS_ASK_ROW)
