from appium.webdriver.common.appiumby import AppiumBy

from base.base_page import BasePage
from base.utils import wait_present

# Toppan's entire onboarding: a "Select Language" modal shown on a fresh install.
#
# Captured 2026-09-02 from the first run that ever wiped toppan. Until then the suite believed the
# wallet had no onboarding at all (wallets/toppan/pages/landing_page.py said so outright), because
# nothing had ever cleared its data — which is also why it had accumulated 14 credentials.
#
# The modal is an Ionic overlay, and while it is up the home screen is **not in the accessibility
# tree at all** — every home element disappears. That is why init_flow's wait for home could never
# succeed after a wipe: not slow, unreachable.
#
# The resource-ids carry Ionic's per-overlay instance counter — the dump showed `alert-2-hdr` and
# `alert-input-2-0`, where the "2" increments per overlay created in the app session. They are
# therefore NOT stable across runs and must never be matched whole. Anchoring on the copy plus the
# stable part of the id is the best available: a wording change in a new build would break this,
# and that is worth saying in any failure raised from here.
SCREEN_ID = (AppiumBy.XPATH, '//*[@text="Select Language"]')

DEFAULT_LANGUAGE = "English (UK)"

# The confirm button has no resource-id at all, so it is matched purely on its label.
_OK = (AppiumBy.XPATH, '//android.widget.Button[@text="OK"]')


def _language_option(language: str):
    return (AppiumBy.XPATH,
            f'//android.widget.RadioButton[contains(@resource-id,"alert-input-")'
            f' and @text="{language}"]')


def on_screen(driver, timeout: float = 2) -> bool:
    return wait_present(driver, SCREEN_ID, timeout=timeout)


class LanguagePage(BasePage):
    def select(self, language: str = DEFAULT_LANGUAGE):
        """Pick a language and confirm, which is the whole of toppan's onboarding.

        `English (UK)` is already selected on a fresh install, but it is tapped anyway rather than
        just pressing OK: relying on the default would silently onboard into whatever language a
        future build ships as first, and every other locator in this wallet matches English copy.
        """
        option = _language_option(language)
        if not wait_present(self.driver, option, timeout=self._get_timeout("default")):
            raise RuntimeError(
                f"[language_page] Toppan's language modal is showing but '{language}' is not one "
                "of its options. Matched on the option's own label, so a renamed or reordered "
                "language list in a new build would look like this."
            )
        self.click(option)
        self.click(_OK)
