"""The FLAG_SECURE guard, with fake drivers — no device.

A wallet that sets FLAG_SECURE on its credential screens is protecting the holder. The harness
must record that as a wallet property and fall back to the XML dump, not report it as a broken
screenshot. By decision (2026-09-04) it never fails a test.
"""
from selenium.common.exceptions import WebDriverException

from base.android import is_secure_screen_refusal
from base import conftest_helpers as helpers


SECURE_REFUSAL = WebDriverException(
    msg="Failed to capture a screenshot. Does the current view have 'secure' flag set?",
    stacktrace=["io.appium.uiautomator2.common.exceptions.TakeScreenshotException"],
)


class _Driver:
    """A driver whose screenshot either works or refuses the way the platform does."""

    def __init__(self, *, secure: bool, source: str = "<hierarchy/>"):
        self._secure = secure
        self.page_source = source
        self.screenshots = []

    def save_screenshot(self, path):
        if self._secure:
            raise SECURE_REFUSAL
        self.screenshots.append(path)
        open(path, "w").close()
        return True


class _Node:
    def __init__(self, name="test_x[heidi]", wallet="heidi"):
        self.name = name
        self.user_properties = []
        self.callspec = type("C", (), {"params": {"driver": wallet}})()


class _Config:
    def __init__(self, tmp_path):
        self._run_dir = tmp_path
        self._protected_wallets = set()


class _Request:
    def __init__(self, tmp_path, wallet="heidi"):
        self.node = _Node(wallet=wallet)
        self.config = _Config(tmp_path)


REPORTING = {"reporting": {"screenshot_on_failure": True, "xml_on_failure": True}}


def test_the_platform_refusal_is_told_apart_from_a_broken_screenshot():
    assert is_secure_screen_refusal(SECURE_REFUSAL)
    assert not is_secure_screen_refusal(WebDriverException(msg="socket hang up"))


def test_a_secure_screen_still_yields_the_xml_dump(tmp_path):
    request = _Request(tmp_path)
    saved = helpers.save_failure_artifacts(_Driver(secure=True), request, REPORTING, wallet="heidi")
    assert saved, "the XML dump must survive a screenshot the platform refuses"
    assert (tmp_path / "xml_dumps" / "test_x_heidi.xml").exists()
    assert not list((tmp_path / "screenshots").glob("*.png")), "no screenshot should be left behind"


def test_a_refusal_is_recorded_as_a_wallet_property_not_swallowed(tmp_path):
    request = _Request(tmp_path)
    helpers.save_failure_artifacts(_Driver(secure=True), request, REPORTING, wallet="heidi")
    assert ("screen_protected", "heidi") in request.node.user_properties
    assert helpers.screen_is_protected(request, "heidi")


def test_a_wallet_that_allows_screenshots_is_never_marked_protected(tmp_path):
    request = _Request(tmp_path, wallet="hovi")
    helpers.save_failure_artifacts(_Driver(secure=False), request, REPORTING, wallet="hovi")
    assert request.node.user_properties == []
    assert not helpers.screen_is_protected(request, "hovi")


def test_the_xml_dump_is_written_before_the_screenshot_is_attempted(tmp_path):
    """Order matters: on a secure screen the dump is the only evidence obtainable."""
    order = []

    class _Recorder(_Driver):
        @property
        def page_source(self):
            order.append("xml")
            return "<hierarchy/>"

        @page_source.setter
        def page_source(self, v):
            pass

        def save_screenshot(self, path):
            order.append("screenshot")
            raise SECURE_REFUSAL

    helpers.save_failure_artifacts(_Recorder(secure=True), _Request(tmp_path), REPORTING,
                                   wallet="heidi")
    assert order == ["xml", "screenshot"]


def test_an_unrelated_screenshot_failure_is_not_mistaken_for_flag_secure(tmp_path):
    class _Broken(_Driver):
        def save_screenshot(self, path):
            raise WebDriverException(msg="socket hang up")

    request = _Request(tmp_path)
    helpers.save_failure_artifacts(_Broken(secure=False), request, REPORTING, wallet="heidi")
    assert request.node.user_properties == [], "a broken driver is not a protected screen"
