import logging
import time

from appium.webdriver.common.appiumby import AppiumBy
from selenium.common.exceptions import TimeoutException, WebDriverException

from base.base_page import BasePage
from base.play_store_analyzer import KeywordPlayStoreAnalyzer, PlayStoreState
from base.utils import get_app_info

logger = logging.getLogger(__name__)

_PLAY_STORE_PKG = "com.android.vending"
_KEYCODE_HOME = 3


class UpdateNotFinished(Exception):
    """An update was started but never confirmed installed.

    Raised only after the Update button has been tapped, so the Play Store may still be
    replacing the package in the background. Every result from the rest of the session is
    then meaningless — the app under test is mid-swap — so this must fail the run rather
    than be logged and stepped over.
    """


class BaseTest:
    def __init__(self, driver, config):
        self.driver = driver
        self.config = config
        self._analyzer = KeywordPlayStoreAnalyzer()

    @property
    def page_args(self):
        """Keyword args for constructing any page object from this test's config."""
        return {
            "debug": self.config.get("debug", False),
            "timeouts": self.config.get("timeouts", {}),
            "device_pin": self.config.get("android", {}).get("device_pin", ""),
        }

    def setup(self) -> bool:
        """Install the app if it is missing. Returns True if an installation was performed."""
        app_package = self.config["application"]["package"]
        if self.driver.is_app_installed(app_package):
            return False
        self._install_app_from_play_store(app_package)
        return True

    def check_for_updates(self, timeout: int = 900) -> dict:
        """Open the app's Play Store page and install a pending update, if any.

        Reuses the same Play Store state machine as installation — the only differences are
        that the button reads "Update" instead of "Install", and that completion cannot be
        detected with ``is_app_installed()`` (already true), so the app's ``versionCode`` is
        polled instead.

        Wallets not published on the Play Store (sideloaded/branded builds) land on an error
        or unrecognised page; that is reported as a warning and never fails the run. Once the
        Update button has been tapped, however, failure to confirm the new build raises
        ``UpdateNotFinished`` — see that exception for why it can't be stepped over.

        Args:
            timeout: Seconds of wall-clock time to wait for the update to finish installing.

        Returns a dict for the run report:
            {"update_available": bool, "updated": bool,
             "version_before": str, "version_after": str}
        """
        app_package = self.config["application"]["package"]
        before = self._version_code(app_package)
        result = {
            "update_available": False,
            "updated": False,
            "version_before": before,
            "version_after": before,
        }

        if before == "unknown":
            # A version change is the only signal that an update landed, so without a
            # readable baseline the check can neither detect nor confirm one. Reporting
            # "up to date" would be a guess and applying an update would be unverifiable.
            logger.warning(
                f"[update] Cannot read the installed versionCode for {app_package} via adb "
                f"(device '{self._device_serial() or 'default'}') — skipping the update check."
            )
            result["update_skipped"] = "versionCode unavailable"
            return result

        state = self._open_play_store_page(app_package, "update")

        if state == PlayStoreState.INSTALLED:
            state = self._recheck_for_update()

        if state == PlayStoreState.INSTALLED:
            logger.info(f"[update] {app_package} is up to date (build {before})")
            self._leave_play_store()
            return result

        if state != PlayStoreState.UPDATE_AVAILABLE:
            logger.warning(
                f"[update] Could not determine update state for {app_package} "
                f"(Play Store page reported '{state.value}') — the app may not be published "
                "on the Play Store. Continuing with the installed build."
            )
            self._leave_play_store()
            return result

        result["update_available"] = True
        logger.info(f"[update] Update available for {app_package} — clicking Update...")
        self._click_primary_action(PlayStoreState.UPDATE_AVAILABLE)

        result["version_after"] = self._wait_for_update_to_finish(
            app_package, before, timeout
        )
        result["updated"] = result["version_after"] != before

        if result["updated"]:
            logger.info(
                f"[update] {app_package} updated: build {before} → {result['version_after']}. "
                "Locators may have changed — treat failures in this run as suspect."
            )
        else:
            logger.warning(
                f"[update] {app_package} still reports build {before} after the update "
                f"finished — the Play Store may have failed silently."
            )

        self._leave_play_store()
        return result

    def _recheck_for_update(self, timeout=15) -> PlayStoreState:
        """Re-read the details page for a while before accepting "up to date".

        "Open" and "Uninstall" render from local package state, but the "Update" button only
        appears once the Play Store has resolved the available version over the network. A
        first read of INSTALLED can therefore be a page that simply hasn't finished loading,
        which would silently report every wallet as current forever.
        """
        deadline = time.time() + timeout
        state = PlayStoreState.INSTALLED
        while time.time() < deadline:
            time.sleep(1)
            state = self._analyzer.get_state(self.driver)
            if state == PlayStoreState.POPUP:
                self._analyzer.dismiss_popup(self.driver)
                continue
            if state != PlayStoreState.INSTALLED:
                logger.info(f"[update] Page settled to '{state.value}' on re-read")
                return state
        return state

    def _wait_for_update_to_finish(self, app_package, version_before, timeout) -> str:
        """Poll until the app's versionCode changes, or the Update button is gone.

        The version code is the authoritative signal — ``is_app_installed()`` is already
        True for an update, and the Play Store button flips to "Open" a moment before
        the new package is actually registered with the system.
        """
        debug = self.config.get("debug", False)
        poll_interval = 2
        # Wall clock, not a counter: a single get_state() costs several seconds (it probes a
        # dozen error strings), so counting poll intervals would overrun `timeout` many-fold.
        started = time.time()
        deadline = started + timeout
        prev_state = None
        settled = 0

        while time.time() < deadline:
            elapsed = int(time.time() - started)
            current = self._version_code(app_package)
            if current != version_before:
                logger.info(f"[update] SUCCESS — new build {current} after {elapsed}s.")
                return current

            try:
                state = self._analyzer.get_state(self.driver)
            except WebDriverException:
                # UiAutomator2 instrumentation is killed while the package is replaced.
                logger.info(f"[update] UiAutomator2 unavailable (installing) — waiting... ({elapsed}s)")
                time.sleep(poll_interval)
                continue

            if state != prev_state:
                logger.info(f"[state] → {state.value}")
                prev_state = state

            if state == PlayStoreState.ERROR:
                description = self._analyzer.get_error_description(self.driver)
                raise UpdateNotFinished(
                    f"Play Store error while updating {app_package}: {description}"
                )

            if state == PlayStoreState.POPUP:
                if debug:
                    self._log_screen_text()
                self._analyzer.dismiss_popup(self.driver)

            elif state == PlayStoreState.INSTALLED:
                # Button flipped back to "Open" with no version change yet — give the
                # package manager a couple of polls to catch up before giving up.
                settled += 1
                if settled >= 3:
                    # One last read: the package may have been committed since the poll
                    # above, and returning a stale build would contradict app_info.json.
                    return self._version_code(app_package)

            elif state == PlayStoreState.DOWNLOADING:
                logger.info(f"[update] Downloading... ({elapsed}s elapsed)")

            elif state == PlayStoreState.INSTALLING:
                logger.info(f"[update] Finalising... ({elapsed}s elapsed)")

            time.sleep(poll_interval)

        raise UpdateNotFinished(
            f"{app_package} was not confirmed updated within {timeout}s (still build "
            f"{version_before}). The Play Store may still be installing it in the background, "
            "so the app under test cannot be trusted for this run."
        )

    def _version_code(self, app_package: str) -> str:
        """Installed versionCode via adb, or "unknown" if it can't be read."""
        return get_app_info(app_package, self._device_serial())["version_code"]

    def _device_serial(self) -> str:
        """ADB serial for adb-based helpers; empty string targets the only device.

        ``udid`` first: device.json allows a device_name that is a friendly label with the
        real serial given separately as ``udid`` (see ``_resolve_device`` in conftest), and
        only the serial is a valid ``adb -s`` target.
        """
        try:
            caps = self.driver.capabilities or {}
            return caps.get("udid") or caps.get("deviceName") or ""
        except Exception:
            return ""

    def _open_play_store_page(self, app_package, label) -> PlayStoreState:
        """Deeplink to the app's Play Store details page and return the detected state."""
        play_store_url = f"https://play.google.com/store/apps/details?id={app_package}"
        logger.info(f"[{label}] Opening Play Store for: {app_package}")
        self.driver.execute_script(
            "mobile: deepLink", {"url": play_store_url, "package": _PLAY_STORE_PKG}
        )
        return self._settle_play_store_state(label)

    def _settle_play_store_state(self, label, timeout=20) -> PlayStoreState:
        """Wait for the details page to render, dismissing popups. Never raises on UNKNOWN."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            state = self._analyzer.get_state(self.driver)

            if state == PlayStoreState.POPUP:
                if self.config.get("debug", False):
                    self._log_screen_text()
                self._analyzer.dismiss_popup(self.driver)
                continue

            if state != PlayStoreState.UNKNOWN:
                logger.info(f"[{label}] Play Store state: {state.value}")
                return state

            time.sleep(0.5)

        logger.warning(f"[{label}] Play Store page did not settle within {timeout}s")
        return PlayStoreState.UNKNOWN

    def _click_primary_action(self, expected: PlayStoreState):
        """Click the app's own action button, refusing to click anything unexpected.

        The related-apps section further down the page carries Install/Update buttons for
        other packages; ``find_primary_action`` picks by position so those are never hit,
        and re-checking the text guards against the page changing under us between the
        state read and the click.
        """
        element, state = self._analyzer.find_primary_action(self.driver)
        if element is None or state != expected:
            found = state.value if state else "no action button"
            raise Exception(
                f"Expected the Play Store's primary button to be '{expected.value}', "
                f"found '{found}'"
            )
        element.click()

    def _leave_play_store(self):
        """Return to the launcher so the caller can activate the wallet cleanly."""
        try:
            self.driver.press_keycode(_KEYCODE_HOME)
        except Exception:
            pass

    def _install_app_from_play_store(self, app_package):
        """Open Play Store and install the app, reacting to each screen state."""
        debug = self.config.get("debug", False)

        self._open_play_store_page(app_package, "install")
        self._wait_for_state(PlayStoreState.READY_TO_INSTALL, timeout=20)

        logger.info("[install] Clicking Install...")
        self._click_primary_action(PlayStoreState.READY_TO_INSTALL)

        timeout = 120
        poll_interval = 2
        elapsed = 0
        prev_state = None

        while elapsed < timeout:
            if self.driver.is_app_installed(app_package):
                logger.info(f"[install] SUCCESS — installed after {elapsed}s.")
                return

            try:
                state = self._analyzer.get_state(self.driver)
            except WebDriverException:
                # UiAutomator2 instrumentation is temporarily killed during installation.
                # This is expected — sleep and let the install finish.
                logger.info(f"[install] UiAutomator2 unavailable (installing) — waiting... ({elapsed}s)")
                time.sleep(poll_interval)
                elapsed += poll_interval
                continue

            if state != prev_state:
                logger.info(f"[state] → {state.value}")
                prev_state = state

            if state == PlayStoreState.ERROR:
                description = self._analyzer.get_error_description(self.driver)
                logger.error(f"[install] STOPPED — {description}")
                raise Exception(f"Play Store error: {description}")

            elif state == PlayStoreState.POPUP:
                if debug:
                    self._log_screen_text()
                self._analyzer.dismiss_popup(self.driver)

            elif state == PlayStoreState.DOWNLOADING:
                try:
                    pb = self.driver.find_element(AppiumBy.CLASS_NAME, "android.widget.ProgressBar")
                    current = int(pb.get_attribute("progress") or 0)
                    maximum = int(pb.get_attribute("max") or 1)
                    logger.info(f"[install] Downloading... {int(current / maximum * 100)}%")
                except Exception:
                    logger.info(f"[install] Downloading... ({elapsed}s elapsed)")

            elif state == PlayStoreState.INSTALLING:
                logger.info(f"[install] Finalising... ({elapsed}s elapsed)")

            elif state == PlayStoreState.UNKNOWN:
                logger.info(f"[install] Unknown state ({elapsed}s elapsed)")
                if debug:
                    self._log_screen_text()

            time.sleep(poll_interval)
            elapsed += poll_interval

        logger.error(f"[install] STOPPED — {app_package} not installed within {timeout}s.")
        raise TimeoutException(f"[install] TIMEOUT: {app_package} not installed within {timeout}s.")

    def _wait_for_state(self, expected: PlayStoreState, timeout=20):
        """Wait until Play Store reaches the expected state. Auto-dismisses popups, raises on ERROR."""
        debug = self.config.get("debug", False)
        deadline = time.time() + timeout
        while time.time() < deadline:
            state = self._analyzer.get_state(self.driver)

            if state == expected:
                logger.info(f"[state] Reached: {state.value}")
                return state

            if state == PlayStoreState.ERROR:
                description = self._analyzer.get_error_description(self.driver)
                logger.error(f"[state] STOPPED — {description}")
                raise Exception(f"Play Store error: {description}")

            if state == PlayStoreState.POPUP:
                if debug:
                    self._log_screen_text()
                self._analyzer.dismiss_popup(self.driver)
                continue

            logger.info(f"[state] Waiting for '{expected.value}', current: '{state.value}'")
            time.sleep(0.5)

        actual = self._analyzer.get_state(self.driver)
        raise TimeoutException(f"Timed out waiting for '{expected.value}', got '{actual.value}'")

    def _log_screen_text(self):
        try:
            elements = self.driver.find_elements(AppiumBy.XPATH, '//*[@text!=""]')
            texts = [el.get_attribute("text") for el in elements if el.get_attribute("text")]
            if texts:
                logger.info(f"[screen] Visible text: {texts}")
        except Exception:
            pass
