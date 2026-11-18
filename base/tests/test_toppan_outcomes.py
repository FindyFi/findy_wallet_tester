"""Every way toppan's flows can end, mapped to the outcome it raises and how far it got.

Before, every toppan branch raised a bare RuntimeError and the report read a reason back out of the
sentence. Its error dialog was found by `android:id/button1` alone — the positive button of any
AlertDialog — a refused deeplink escaped as a raw driver exception, and two branches after Share
or Accept logged and returned, so a wallet that never confirmed anything passed.

Two layers, neither touching a device:

- the **page probes** run for real against copies of toppan's captured dumps, through a driver
  that evaluates their XPath with ElementTree;
- the **flows** run for real against `Phone`, which answers each probe from a scripted sequence
  of screens and keeps a virtual clock, so a 30-second wait costs nothing and every timeout the
  flow passes is visible.
"""
import inspect
import time
import xml.etree.ElementTree as ET
from types import SimpleNamespace

import pytest
from appium.webdriver.common.appiumby import AppiumBy
from selenium.common.exceptions import NoSuchElementException, WebDriverException

from base import deeplink, outcome, progress
from base.android import SystemOverlay
from base.conftest_helpers import _QUOTED
from base.screens import UNKNOWN
from wallets.toppan.flows import _failures as failures
from wallets.toppan.flows import credential_flow as cf, init_flow, verification_flow as vf
from wallets.toppan.pages import error_dialog, result_page
from wallets.toppan.screens import SCREENS

PACKAGE = "com.toppansecurity.walletapp"
URL = "openid-credential-offer://?credential_offer_uri=https%3A%2F%2Fissuer.example"
PAGE_ARGS = {"timeouts": {"default": 10, "credential_offer": 30}}

# The real shape, as Appium reports a refused intent.
REFUSED = ("Error attempting to start URI. Original error: Error: Starting: Intent "
           "{ act=android.intent.action.VIEW dat=https://paradym.id/... pkg=" + PACKAGE + " }\n"
           "Error: Activity not started, unable to resolve Intent "
           "{ act=android.intent.action.VIEW dat=https://paradym.id/... flg=0x10000000 "
           "pkg=" + PACKAGE + " }")
DEAD = WebDriverException("instrumentation process is not running")

# toppan's own words, from the dumps.
JSON_EOF = ("kotlinx.serialization.json.internal.JsonDecodingException: Expected start of the "
            "object '{', but had 'EOF' instead at path: $\nJSON input: ")
NO_DOCTYPE = "Request docType urn:vc:authbound:pension:1.0 is not available"


def wallet_said(exc) -> str:
    """What the app.log digest would publish as the wallet's words: the first quoted span."""
    m = _QUOTED.search(str(exc))
    return m.group(1) if m else ""


# --- captured screens ----------------------------------------------------------------------------
#
# Structure and attributes copied from the dumps (other attributes dropped). The error dialog:
# PUBLISH_findy_wallet_tester/reports/2026-10-15_22-00-02/toppan/xml_dumps/
# test_credential_verification_waltid_verifier_pension_verification-toppan.xml. The result screen:
# the 2026-09-16 nightly's authbound_verifier dump and reports/2026-08-13_10-20-41's.

def _dialog(message, *, package=PACKAGE, buttons=(("android:id/button1", "OK"),)):
    p = f'package="{package}"'
    keys = "".join(f'<android.widget.Button resource-id="{rid}" text="{label}" {p}/>'
                   for rid, label in buttons)
    return (
        f'<hierarchy><android.widget.FrameLayout {p}><android.widget.FrameLayout {p}>'
        f'<android.widget.FrameLayout resource-id="android:id/content" {p}>'
        f'<android.widget.LinearLayout resource-id="android:id/parentPanel" {p}>'
        f'<android.widget.FrameLayout resource-id="android:id/contentPanel" {p}>'
        f'<android.widget.ScrollView resource-id="android:id/scrollView" {p}>'
        f'<android.widget.LinearLayout {p}>'
        f'<android.view.View resource-id="android:id/textSpacerNoTitle" {p}/>'
        f'<android.widget.TextView resource-id="android:id/message" text="{message}" {p}/>'
        f'</android.widget.LinearLayout></android.widget.ScrollView></android.widget.FrameLayout>'
        f'<android.widget.ScrollView resource-id="android:id/buttonPanel" {p}>'
        f'<android.widget.LinearLayout {p}>{keys}</android.widget.LinearLayout>'
        f'</android.widget.ScrollView></android.widget.LinearLayout></android.widget.FrameLayout>'
        f'</android.widget.FrameLayout></android.widget.FrameLayout></hierarchy>'
    )


def _result(message):
    p = f'package="{PACKAGE}"'
    return (
        f'<hierarchy><android.widget.FrameLayout {p}><android.webkit.WebView {p}>'
        f'<android.view.View {p}><android.view.View {p}>'
        f'<android.widget.TextView resource-id="background-content" {p}/>'
        f'<android.view.View {p}><android.widget.Image {p}/>'
        f'<android.widget.TextView text="{message}" {p}/>'
        f'<android.view.View {p}><android.widget.Button text="Back to Home Page" {p}/>'
        f'</android.view.View></android.view.View></android.view.View></android.view.View>'
        f'</android.webkit.WebView></android.widget.FrameLayout></hierarchy>'
    )


HOME = (f'<hierarchy><android.widget.FrameLayout package="{PACKAGE}">'
        f'<android.widget.TextView text="TOPPAN Wallet" package="{PACKAGE}"/>'
        f'</android.widget.FrameLayout></hierarchy>')


class _Node:
    def __init__(self, screen, node):
        self._screen, self._node = screen, node

    @property
    def text(self):
        return self._node.get("text", "")

    def get_attribute(self, name):
        return self._node.get(name, "")

    def click(self):
        self._screen.tapped.append(self._node.get("text", ""))


class Screen:
    """One captured dump as a driver. XPath goes through ElementTree, so a page locator written in
    a shape ElementTree cannot read fails here rather than matching nothing quietly."""
    current_package = PACKAGE
    capabilities: dict = {}

    def __init__(self, xml):
        self.root = ET.fromstring(xml.replace("\n", "&#10;"))
        self.tapped = []

    def find_elements(self, by, value):
        assert by == AppiumBy.XPATH and value.startswith("//"), value
        return [_Node(self, n) for n in self.root.findall("." + value)]

    def find_element(self, by, value):
        found = self.find_elements(by, value)
        if not found:
            raise NoSuchElementException(value)
        return found[0]


# --- toppan, scripted, on a virtual clock --------------------------------------------------------

class Phone:
    """What toppan shows, as a driver whose probes are answered from a script.

    `show(...)` sets what is on screen now, and `then` maps a tap to what follows it. Either is a
    list of (screen, length) segments, the last of which stays: an int length is a number of looks
    by any probe, a float one is seconds on the clock since the screens were shown. A look at a
    screen that is not there advances the clock by the probe's timeout, as `wait_present` would;
    one that is there costs nothing.
    """
    capabilities: dict = {}

    def __init__(self):
        self.clock = 1000.0
        self.shown_at = self.clock
        self.front = PACKAGE
        self.app_state = 3
        self.packages = []
        self.refuse = None
        self.dead_after = None
        self.crash = None
        self.dialog_text = "Unsupported client_id prefix"
        self.result_text = ""
        self.result_shared = False
        self.then = {}
        self.asked, self.taps = [], []
        self.show(("", None))

    @property
    def current_package(self):
        if isinstance(self.front, BaseException):
            raise self.front
        return self.front

    def query_app_state(self, package):
        return self.app_state

    def show(self, *segments):
        self.segments, self.looks, self.shown_at = list(segments), 0, self.clock

    def now(self):
        return self.clock

    def screen(self):
        n, elapsed = self.looks, self.clock - self.shown_at
        for name, length in self.segments:
            if isinstance(length, float):
                if elapsed < length:
                    return name
                elapsed -= length
            elif length is None or n < length:
                return name
            else:
                n -= length
        return self.segments[-1][0]

    def probe(self, name):
        def look(driver, timeout=2, package=None):
            self.asked.append((name, timeout))
            if package is not None:
                self.packages.append(package)
            if self.dead_after is not None and len(self.asked) > self.dead_after:
                raise DEAD
            shown = self.screen() == name
            self.looks += 1
            if not shown:
                self.clock += timeout
            return shown
        return look

    def tap(self, name):
        self.taps.append(name)
        if name in self.then:
            self.show(*self.then[name])

    def execute_script(self, script, args):
        if self.refuse:
            raise WebDriverException(self.refuse)

    def waited(self, name):
        return [t for n, t in self.asked if n == name]


def _page(phone, **taps):
    class Page:
        def __init__(self, driver, **kw):
            pass
    for method, label in taps.items():
        setattr(Page, method, lambda self, label=label: phone.tap(label))
    return Page


@pytest.fixture
def phone(monkeypatch):
    p = Phone()
    progress.reset()
    monkeypatch.setattr(time, "monotonic", p.now)
    monkeypatch.setattr(error_dialog, "present", p.probe("error"))
    monkeypatch.setattr(error_dialog, "message",
                        lambda d, package: " ".join(p.dialog_text.split()))
    monkeypatch.setattr(result_page, "on_screen", p.probe("result"))
    monkeypatch.setattr(result_page, "message", lambda d: p.result_text)
    monkeypatch.setattr(result_page, "back_home", lambda d: p.tap("Back to Home Page"))

    def shared(d, timeout=1):
        p.asked.append(("shared", timeout))
        return p.result_shared
    monkeypatch.setattr(result_page, "shared", shared)
    monkeypatch.setattr(failures, "home_on_screen", p.probe("home"))
    monkeypatch.setattr(failures, "handle_permission_if_present", lambda d: False)
    monkeypatch.setattr(failures, "detect_crash_or_anr", lambda d, *a, **k: p.crash)

    monkeypatch.setattr(cf, "confirmation_on_screen", p.probe("confirm"))
    monkeypatch.setattr(cf, "offer_on_screen", p.probe("offer"))
    monkeypatch.setattr(cf, "processing_on_screen", p.probe("processing"))
    monkeypatch.setattr(cf, "home_on_screen", p.probe("home"))
    monkeypatch.setattr(cf, "ConfirmationPage", _page(p, confirm="CONTINUE"))
    monkeypatch.setattr(cf, "CredentialOfferPage", _page(p, accept="Accept"))

    monkeypatch.setattr(vf, "confirmation_on_screen", p.probe("confirm"))
    monkeypatch.setattr(vf, "request_on_screen", p.probe("request"))
    monkeypatch.setattr(vf, "home_on_screen", p.probe("home"))
    monkeypatch.setattr(vf, "VerificationConfirmationPage", _page(p, confirm="CONTINUE"))
    monkeypatch.setattr(vf, "VerificationRequestPage", _page(p, share="Share"))

    monkeypatch.setattr(deeplink, "unroutable_reason", lambda *a, **k: "")
    monkeypatch.setattr(deeplink, "registered_schemes", lambda *a, **k: [])
    monkeypatch.setattr(outcome, "detect_crash_or_anr", lambda *a, **k: None)
    yield p
    progress.reset()


def _issue(phone, package=PACKAGE):
    cf.run(phone, provider=SimpleNamespace(get=lambda n: URL),
           credential_name="credential_issuance", app_package=package, **PAGE_ARGS)


def _verify(phone):
    vf.run(phone, provider=SimpleNamespace(get=lambda n: URL),
           credential_name="pension_verification", app_package=PACKAGE, **PAGE_ARGS)


def _raised(fn, *args):
    with pytest.raises(outcome.FlowFailure) as exc:
        fn(*args)
    return exc.value, progress.snapshot()


def _issuance_until_accept(phone, after_accept):
    phone.show(("confirm", None))
    phone.then = {"CONTINUE": [("offer", None)], "Accept": after_accept}


# --- the page probes, against the captured dumps -------------------------------------------------

def test_the_error_probe_matches_toppans_dialog_and_reads_its_message_on_one_line():
    screen = Screen(_dialog(JSON_EOF))
    assert error_dialog.present(screen, timeout=0.1, package=PACKAGE)
    assert error_dialog.message(screen, package=PACKAGE) == " ".join(JSON_EOF.split())


@pytest.mark.parametrize("xml", [
    _dialog("Allow access?", package="android"),        # Android's own dialog: button1 "OK" too
    _dialog("Do you want to proceed with verification?",
            buttons=(("android:id/button2", "CANCEL"), ("android:id/button1", "CONTINUE"))),
    _result("No document shared"),
    HOME,
], ids=["system-dialog", "two-button-dialog", "result-screen", "home"])
def test_the_error_probe_is_anchored_on_toppan_and_its_single_ok(xml):
    """`button1` alone matched every AlertDialog's positive button."""
    screen = Screen(xml)
    assert not error_dialog.present(screen, timeout=0.1, package=PACKAGE)
    assert error_dialog.message(screen, package=PACKAGE) == ""


def test_the_error_probe_follows_the_package_it_is_given():
    """A renamed package must move the anchor with it, not blind the probe."""
    screen = Screen(_dialog("Unsupported client_id prefix", package="com.example.renamed"))
    assert not error_dialog.present(screen, timeout=0.1, package=PACKAGE)
    assert error_dialog.present(screen, timeout=0.1, package="com.example.renamed")


def test_the_result_screen_reads_toppans_own_no_match_words():
    screen = Screen(_result(NO_DOCTYPE))
    assert result_page.on_screen(screen, timeout=0.1)
    assert result_page.message(screen) == NO_DOCTYPE
    assert result_page.no_match(screen, timeout=0.1)


def test_no_document_shared_is_not_a_no_match():
    screen = Screen(_result("No document shared"))
    assert result_page.message(screen) == "No document shared"
    assert not result_page.no_match(screen, timeout=0.1)


def test_leaving_the_result_screen_taps_its_own_button():
    screen = Screen(_result("No document shared"))
    result_page.back_home(screen)
    assert screen.tapped == ["Back to Home Page"]


def test_screens_declares_only_what_a_dump_shows():
    dialog = Screen(_dialog("Unsupported client_id prefix"))
    assert SCREENS.error(dialog, 0.1) and not SCREENS.error(Screen(HOME), 0.1)
    assert SCREENS.error_text(dialog) == "Unsupported client_id prefix"
    assert SCREENS.no_match is result_page.no_match
    assert SCREENS.processing is UNKNOWN and SCREENS.success is UNKNOWN


# --- issuance -----------------------------------------------------------------------------------

def test_a_refused_issuance_deeplink_is_unroutable_not_a_raw_driver_exception(phone):
    phone.refuse = REFUSED
    exc, trail = _raised(_issue, phone)
    assert exc.category == outcome.UNROUTABLE
    assert str(exc).endswith("[no_retry]")
    assert trail["stage"] == ""


def test_a_deeplink_failure_android_did_not_explain_is_ours(phone):
    phone.refuse = "adb: device offline"
    exc, _ = _raised(_issue, phone)
    assert exc.category == outcome.LOST_SESSION


def test_an_error_dialog_instead_of_the_confirmation_is_rejected_at_open_quoting_toppan(phone):
    phone.dialog_text = JSON_EOF
    phone.show(("error", None))
    exc, trail = _raised(_issue, phone)
    assert phone.clock - 1000 < 5, "the error dialog ends the wait; it is not found at the end"
    assert exc.category == outcome.REJECTED
    assert wallet_said(exc) == " ".join(JSON_EOF.split())
    assert "\n" not in str(exc)
    assert str(exc).endswith("[no_retry]")
    assert trail["stage"] == progress.OPEN


def test_an_error_dialog_after_continue_is_rejected_after_the_tap(phone):
    phone.show(("confirm", None))
    phone.then = {"CONTINUE": [("error", None)]}
    exc, trail = _raised(_issue, phone)
    assert exc.category == outcome.REJECTED
    assert str(exc).startswith("[rejected] [credential_flow] toppan could not process the "
                               "credential offer for 'credential_issuance': \"Unsupported")
    assert phone.taps == ["CONTINUE"]
    assert trail["stage"] == progress.OPEN
    assert trail["steps"][-1].endswith("tapped CONTINUE")


def test_the_tamper_check_is_a_harness_failure_not_a_verdict_on_the_issuer(phone):
    phone.dialog_text = failures.THREAT
    phone.show(("error", None))
    exc, _ = _raised(_issue, phone)
    assert exc.category == outcome.HARNESS
    assert wallet_said(exc) == "Threat detected"
    assert "[no_retry]" not in str(exc)


def test_a_gateway_failure_after_accept_is_rejected_but_may_be_retried(phone):
    phone.dialog_text = "Token request failed with 502 Bad Gateway+ description=N/A"
    _issuance_until_accept(phone, [("processing", 2), ("error", None)])
    exc, trail = _raised(_issue, phone)
    assert exc.category == outcome.REJECTED
    assert "took the Accept for 'credential_issuance' and then failed" in str(exc)
    assert wallet_said(exc).startswith("Token request failed with 502")
    assert "[no_retry]" not in str(exc)
    assert trail["stage"] == progress.PRESENT


def test_an_unreadable_error_dialog_quotes_nothing_and_may_be_retried(phone):
    phone.dialog_text = ""
    phone.show(("error", None))
    exc, _ = _raised(_issue, phone)
    assert exc.category == outcome.REJECTED
    assert wallet_said(exc) == ""
    assert "could not be read" in str(exc)
    assert "[no_retry]" not in str(exc)


def test_a_stored_credential_completes_accept_and_leaves_keep_to_the_count(phone):
    _issuance_until_accept(phone, [("processing", 3), ("home", None)])
    _issue(phone)
    trail = progress.snapshot()
    assert phone.taps == ["CONTINUE", "Accept"]
    assert trail["stage"] == progress.ACCEPT
    assert any(s.endswith("toppan showed its processing overlay") for s in trail["steps"])
    # An overlay seen and gone is toppan finishing: no further wait for the settle period.
    assert phone.clock - phone.shown_at < 5.0


def test_the_wallet_screen_is_not_taken_as_done_before_toppan_had_time_to_start(phone):
    """The wallet screen sits behind the offer: without the overlay, home counts only after
    `_SETTLE`, as the old flow's 5-second wait for the overlay did."""
    _issuance_until_accept(phone, [("home", None)])
    phone.clock_at_accept = None
    original = phone.tap

    def tap(name):
        original(name)
        if name == "Accept":
            phone.clock_at_accept = phone.clock
    phone.tap = tap
    _issue(phone)
    assert phone.clock - phone.clock_at_accept >= 5.0
    assert progress.snapshot()["stage"] == progress.ACCEPT


def test_nothing_after_accept_is_no_verdict_rather_than_nothing_appeared(phone):
    _issuance_until_accept(phone, [("", None)])
    exc, trail = _raised(_issue, phone)
    assert exc.category == outcome.PROCESSING
    assert "neither its wallet screen nor an error dialog in the 30s after Accept" in str(exc)
    assert trail["stage"] == progress.PRESENT


def test_an_overlay_still_up_at_the_end_is_said_as_seen_when_checked(phone):
    _issuance_until_accept(phone, [("processing", None)])
    exc, _ = _raised(_issue, phone)
    assert exc.category == outcome.PROCESSING
    assert "processing overlay was still up when checked 30s after Accept" in str(exc)


def test_a_crash_after_accept_is_crashed_not_processing(phone):
    _issuance_until_accept(phone, [("", None)])
    phone.crash = SystemOverlay.APP_CRASH
    exc, _ = _raised(_issue, phone)
    assert exc.category == outcome.CRASHED


def test_a_dead_session_after_accept_is_ours(phone):
    _issuance_until_accept(phone, [("processing", None)])
    phone.dead_after = 6
    exc, _ = _raised(_issue, phone)
    assert exc.category == outcome.LOST_SESSION


def test_no_confirmation_on_the_wallet_screen_is_dismissed_at_open(phone):
    phone.show(("home", None))
    exc, trail = _raised(_issue, phone)
    assert exc.category == outcome.DISMISSED
    assert "when checked 30s after the deeplink" in str(exc)
    assert trail["stage"] == progress.OPEN
    assert 30 <= phone.clock - 1000 < 35


def test_no_confirmation_with_toppan_elsewhere_is_the_shared_absent_diagnosis(phone):
    phone.front = "com.android.chrome"
    exc, trail = _raised(_issue, phone)
    assert exc.category == outcome.ABSENT
    assert "no credential offer, no error and no home screen" in str(exc)
    assert "[no_retry]" not in str(exc)
    assert trail["stage"] == progress.DELIVER


def test_no_confirmation_with_toppan_in_front_on_an_unknown_screen_is_processing(phone):
    exc, trail = _raised(_issue, phone)
    assert exc.category == outcome.PROCESSING
    assert "No processing/success screen is mapped for toppan" in str(exc)
    assert trail["stage"] == progress.OPEN


def test_a_late_confirmation_continues_the_flow(phone):
    # Absent for the whole 30-second wait, there for the re-probe after it.
    phone.show(("", 30.0), ("confirm", None))
    phone.then = {"CONTINUE": [("offer", None)], "Accept": [("processing", 2), ("home", None)]}
    _issue(phone)
    assert phone.taps == ["CONTINUE", "Accept"]
    assert any(s.endswith("toppan showed its issuance confirmation late")
               for s in progress.snapshot()["steps"])


def test_an_error_dialog_that_comes_after_the_wait_is_still_rejected(phone):
    phone.show(("", 30.0), ("error", None))
    exc, trail = _raised(_issue, phone)
    assert exc.category == outcome.REJECTED
    assert trail["stage"] == progress.OPEN


def test_an_accept_answered_after_the_wait_still_counts(phone):
    _issuance_until_accept(phone, [("", 30.0), ("home", None)])
    _issue(phone)
    assert progress.snapshot()["stage"] == progress.ACCEPT


def test_a_dead_session_on_the_continue_tap_is_ours(phone, monkeypatch):
    class Dead:
        def __init__(self, driver, **kw):
            pass

        def confirm(self):
            raise DEAD
    monkeypatch.setattr(cf, "ConfirmationPage", Dead)
    phone.show(("confirm", None))
    exc, _ = _raised(_issue, phone)
    assert exc.category == outcome.LOST_SESSION


def test_a_dead_session_while_looking_for_a_permission_dialog_is_ours(phone, monkeypatch):
    def dead(d):
        raise DEAD
    monkeypatch.setattr(failures, "handle_permission_if_present", dead)
    exc, _ = _raised(_issue, phone)
    assert exc.category == outcome.LOST_SESSION


def test_toppan_gone_after_continue_has_left_rather_than_never_come_forward(phone):
    """raise_for's "did not come to the foreground" would contradict the CONTINUE just tapped."""
    phone.show(("confirm", None))
    phone.then = {"CONTINUE": [("", None)]}
    original = phone.tap

    def tap(name):
        original(name)
        phone.front = "com.android.chrome"
    phone.tap = tap
    exc, trail = _raised(_issue, phone)
    assert exc.category == outcome.ABSENT
    assert "had left the foreground when checked 30s after CONTINUE" in str(exc)
    assert "com.android.chrome was in front" in str(exc)
    assert "did not come to the foreground" not in str(exc)
    assert trail["stage"] == progress.OPEN


def test_toppan_in_front_on_an_unknown_screen_after_continue_is_processing(phone):
    phone.show(("confirm", None))
    phone.then = {"CONTINUE": [("", None)]}
    exc, _ = _raised(_issue, phone)
    assert exc.category == outcome.PROCESSING
    assert "was in front when the 30s wait for 'credential_issuance' ran out" in str(exc)


def test_a_crash_instead_of_the_offer_is_crashed(phone):
    phone.show(("confirm", None))
    phone.then = {"CONTINUE": [("", None)]}
    phone.crash = SystemOverlay.ANR
    exc, _ = _raised(_issue, phone)
    assert exc.category == outcome.CRASHED
    assert "stopped responding after CONTINUE" in str(exc)


def test_a_dead_session_while_waiting_for_the_offer_is_ours(phone):
    phone.show(("confirm", None))
    phone.then = {"CONTINUE": [("", None)]}
    phone.dead_after = 4
    exc, _ = _raised(_issue, phone)
    assert exc.category == outcome.LOST_SESSION


# --- verification -------------------------------------------------------------------------------

def _verification(phone, *, request, after_share=(("result", None),)):
    phone.show(("confirm", None))
    phone.then = {"CONTINUE": list(request), "Share": list(after_share)}


def test_a_confirmed_share_completes_accept_and_leaves_the_result_screen(phone):
    _verification(phone, request=[("request", None)])
    phone.result_shared = True
    _verify(phone)
    assert phone.taps == ["CONTINUE", "Share", "Back to Home Page"]
    assert progress.snapshot()["stage"] == progress.ACCEPT


def test_the_request_without_a_confirmation_still_goes_through(phone):
    phone.show(("request", None))
    phone.then = {"Share": [("result", None)]}
    phone.result_shared = True
    _verify(phone)
    assert phone.taps == ["Share", "Back to Home Page"]


def test_a_doctype_toppan_does_not_hold_is_no_match_in_its_own_words(phone):
    _verification(phone, request=[("result", None)])
    phone.result_text = NO_DOCTYPE
    exc, trail = _raised(_verify, phone)
    assert exc.category == outcome.NO_MATCH
    assert wallet_said(exc) == NO_DOCTYPE
    assert str(exc).startswith("[no_match] [verification_flow] toppan received the request for "
                               "'pension_verification' and answered")
    assert str(exc).endswith("[no_retry]")
    # Left up, so the failure screenshot and dump show toppan's verdict; teardown closes it.
    assert phone.taps == ["CONTINUE"]
    assert trail["stage"] == progress.OPEN


def test_no_document_shared_before_any_request_is_rejected(phone):
    _verification(phone, request=[("result", None)])
    phone.result_text = "No document shared"
    exc, trail = _raised(_verify, phone)
    assert exc.category == outcome.REJECTED
    assert wallet_said(exc) == "No document shared"
    assert str(exc).endswith("[no_retry]")
    assert "Back to Home Page" not in phone.taps
    assert trail["stage"] == progress.OPEN


def test_no_document_shared_after_share_is_rejected_after_the_tap(phone):
    _verification(phone, request=[("request", None)])
    phone.result_text = "No document shared"
    exc, trail = _raised(_verify, phone)
    assert exc.category == outcome.REJECTED
    assert "took the Share for 'pension_verification' and then reported" in str(exc)
    assert wallet_said(exc) == "No document shared"
    assert phone.taps == ["CONTINUE", "Share"], "the verdict stays on screen for the evidence"
    assert trail["stage"] == progress.PRESENT
    assert trail["steps"][-1].endswith("tapped Share")


def test_a_result_screen_without_success_or_message_is_rejected_unquoted(phone):
    _verification(phone, request=[("request", None)])
    exc, _ = _raised(_verify, phone)
    assert exc.category == outcome.REJECTED
    assert wallet_said(exc) == ""
    assert "[no_retry]" not in str(exc)


def test_an_error_dialog_after_share_is_rejected(phone):
    _verification(phone, request=[("request", None)], after_share=[("error", None)])
    exc, _ = _raised(_verify, phone)
    assert exc.category == outcome.REJECTED
    assert "took the Share for 'pension_verification' and then failed" in str(exc)


def test_the_wallet_screen_after_share_is_no_longer_a_pass(phone):
    """The old flow logged "complete — home screen reached" and returned: a false green."""
    _verification(phone, request=[("request", None)], after_share=[("home", None)])
    exc, trail = _raised(_verify, phone)
    assert exc.category == outcome.PROCESSING
    assert ("showed its wallet screen, not its result screen, when checked 30s after Share"
            in str(exc))
    assert trail["stage"] == progress.PRESENT
    assert phone.waited("home") == [1.0]


def test_nothing_after_share_is_no_verdict_rather_than_nothing_appeared(phone):
    _verification(phone, request=[("request", None)], after_share=[("", None)])
    exc, _ = _raised(_verify, phone)
    assert exc.category == outcome.PROCESSING
    assert "neither its result screen nor an error dialog in the 30s after Share" in str(exc)


def test_wallet_screen_after_continue_is_dismissed_after_the_default_timeout(phone):
    """2026-09-02 nightly, authbound_verifier: toppan went home after CONTINUE."""
    _verification(phone, request=[("home", None)])
    exc, trail = _raised(_verify, phone)
    assert exc.category == outcome.DISMISSED
    assert "not the verification request, when checked 10s after CONTINUE" in str(exc)
    assert trail["stage"] == progress.OPEN


def test_an_error_dialog_after_continue_is_rejected_quoting_toppan(phone):
    phone.dialog_text = "JAR is signed with EdDSA which is not supported"
    _verification(phone, request=[("error", None)])
    exc, _ = _raised(_verify, phone)
    assert exc.category == outcome.REJECTED
    assert phone.clock - phone.shown_at < 5, "the error dialog ends the wait"
    assert wallet_said(exc) == "JAR is signed with EdDSA which is not supported"
    assert str(exc).endswith("[no_retry]")


def test_an_error_dialog_instead_of_the_confirmation_ends_the_wait_at_once(phone):
    phone.show(("error", None))
    exc, trail = _raised(_verify, phone)
    assert exc.category == outcome.REJECTED
    assert phone.clock - 1000 < 5
    assert trail["stage"] == progress.OPEN


def test_a_refused_verification_deeplink_is_unroutable(phone):
    phone.refuse = REFUSED
    exc, _ = _raised(_verify, phone)
    assert exc.category == outcome.UNROUTABLE


def test_a_result_that_comes_after_the_wait_still_counts(phone):
    _verification(phone, request=[("request", None)], after_share=[("", 30.0), ("result", None)])
    phone.result_shared = True
    _verify(phone)
    assert progress.snapshot()["stage"] == progress.ACCEPT


def test_a_crash_after_share_is_crashed(phone):
    _verification(phone, request=[("request", None)], after_share=[("", None)])
    phone.crash = SystemOverlay.APP_CRASH
    exc, _ = _raised(_verify, phone)
    assert exc.category == outcome.CRASHED
    assert "crashed after Share" in str(exc)


def test_a_dead_session_on_the_share_tap_is_ours(phone, monkeypatch):
    class Dead:
        def __init__(self, driver, **kw):
            pass

        def share(self):
            raise DEAD
    monkeypatch.setattr(vf, "VerificationRequestPage", Dead)
    phone.show(("request", None))
    exc, trail = _raised(_verify, phone)
    assert exc.category == outcome.LOST_SESSION
    assert trail["stage"] == progress.PRESENT


def test_a_dead_session_after_share_is_ours(phone):
    _verification(phone, request=[("request", None)], after_share=[("", None)])
    phone.dead_after = 5
    exc, _ = _raised(_verify, phone)
    assert exc.category == outcome.LOST_SESSION


def test_an_answered_permission_dialog_is_a_step_not_a_stage(phone, monkeypatch):
    monkeypatch.setattr(failures, "handle_permission_if_present", lambda d: True)
    _verification(phone, request=[("request", None)])
    phone.result_shared = True
    _verify(phone)
    steps = progress.snapshot()["steps"]
    assert any(s == "deliver: answered an Android permission dialog" for s in steps)


# --- review round: evidence kept, every lost-session wrap, the last error look --------------------

class _Teardown:
    """A driver for init_flow: on the result screen until "Back to Home Page" is tapped."""
    current_package = PACKAGE

    def __init__(self):
        self.on_result, self.backs, self.tapped = True, 0, []

    def back(self):
        self.backs += 1

    def activate_app(self, package):
        pass

    def terminate_app(self, package):
        raise AssertionError("the result screen must be closed by its button, not a restart")


def test_teardown_closes_the_result_screen_through_its_button_not_back(monkeypatch):
    d = _Teardown()
    monkeypatch.setattr(init_flow.language_page, "on_screen", lambda drv, timeout=2: False)
    monkeypatch.setattr(init_flow, "wait_present", lambda drv, loc, timeout=2: not d.on_result)
    monkeypatch.setattr(init_flow.result_page, "on_screen", lambda drv, timeout=2: d.on_result)

    def back_home(drv):
        d.tapped.append("Back to Home Page")
        d.on_result = False
    monkeypatch.setattr(init_flow.result_page, "back_home", back_home)
    init_flow.run(d, skip_if_done=True, app_package=PACKAGE, timeouts={"default": 1})
    assert d.tapped == ["Back to Home Page"]
    assert d.backs == 0


def _dead_page(**methods):
    class Dead:
        def __init__(self, driver, **kw):
            pass
    for name in methods:
        setattr(Dead, name, lambda self: (_ for _ in ()).throw(DEAD))
    return Dead


def test_a_dead_session_on_the_verification_continue_tap_is_ours(phone, monkeypatch):
    monkeypatch.setattr(vf, "VerificationConfirmationPage", _dead_page(confirm=True))
    phone.show(("confirm", None))
    exc, _ = _raised(_verify, phone)
    assert exc.category == outcome.LOST_SESSION


def test_a_dead_session_on_the_accept_tap_is_ours(phone, monkeypatch):
    monkeypatch.setattr(cf, "CredentialOfferPage", _dead_page(accept=True))
    _issuance_until_accept(phone, [("home", None)])
    exc, trail = _raised(_issue, phone)
    assert exc.category == outcome.LOST_SESSION
    assert trail["stage"] == progress.PRESENT


def test_a_dead_session_reading_the_result_after_share_is_ours(phone, monkeypatch):
    def dead(d, timeout=1):
        raise DEAD
    monkeypatch.setattr(result_page, "shared", dead)
    _verification(phone, request=[("request", None)])
    exc, _ = _raised(_verify, phone)
    assert exc.category == outcome.LOST_SESSION


def test_a_dead_session_at_the_closing_home_look_is_ours(phone, monkeypatch):
    def dead(d, timeout=2):
        raise DEAD
    monkeypatch.setattr(failures, "home_on_screen", dead)
    exc, _ = _raised(_issue, phone)
    assert exc.category == outcome.LOST_SESSION


def test_a_dead_session_reading_the_foreground_after_continue_is_ours(phone):
    phone.show(("confirm", None))
    phone.then = {"CONTINUE": [("", None)]}
    original = phone.tap

    def tap(name):
        original(name)
        phone.front = DEAD
    phone.tap = tap
    exc, _ = _raised(_issue, phone)
    assert exc.category == outcome.LOST_SESSION


def test_a_dead_session_at_the_crash_look_is_ours_not_processing(phone, monkeypatch):
    def dead(d, *a, **k):
        raise DEAD
    monkeypatch.setattr(failures, "detect_crash_or_anr", dead)
    _issuance_until_accept(phone, [("", None)])
    exc, _ = _raised(_issue, phone)
    assert exc.category == outcome.LOST_SESSION


def test_a_crash_look_that_fails_otherwise_claims_nothing(phone, monkeypatch):
    def broken(d, *a, **k):
        raise ValueError("not a driver error")
    monkeypatch.setattr(failures, "detect_crash_or_anr", broken)
    _issuance_until_accept(phone, [("", None)])
    exc, _ = _raised(_issue, phone)
    assert exc.category == outcome.PROCESSING


def test_an_error_dialog_after_the_wallet_screen_returns_is_rejected_not_accepted(phone):
    """The old flow's 70 "[after processing]" failures: the dialog follows the overlay."""
    # The overlay for two looks; the wallet screen for the next three, the last of which is the
    # home probe that ends the wait; then the dialog, for the closing error look.
    _issuance_until_accept(phone, [("processing", 2), ("home", 3), ("error", None)])
    exc, trail = _raised(_issue, phone)
    assert exc.category == outcome.REJECTED
    assert "took the Accept" in str(exc)
    assert trail["stage"] == progress.PRESENT
    assert phone.asked[-1] == ("error", cf._LAST_ERROR_LOOK)


def test_the_flows_anchor_the_error_dialog_on_the_package_they_were_given(phone):
    phone.show(("error", None))
    with pytest.raises(outcome.FlowFailure):
        _issue(phone, package="com.example.renamed")
    assert phone.packages and set(phone.packages) == {"com.example.renamed"}


def test_the_late_reprobe_is_about_one_second_in_all(phone):
    _verification(phone, request=[("home", None)])
    with pytest.raises(outcome.FlowFailure):
        _verify(phone)
    asked = phone.asked
    last_wait_error = max(i for i, a in enumerate(asked) if a == ("error", 0.5))
    closing_error = asked.index(("error", 1.0), last_wait_error)
    late = asked[last_wait_error + 1:closing_error]
    assert [n for n, _ in late] == ["request", "result"]
    assert sum(t for _, t in late) <= 1.0


def test_toppan_gone_after_continue_says_what_android_reports(phone):
    phone.show(("confirm", None))
    phone.then = {"CONTINUE": [("", None)]}
    phone.app_state = 1
    original = phone.tap

    def tap(name):
        original(name)
        phone.front = "com.android.launcher"
    phone.tap = tap
    exc, _ = _raised(_issue, phone)
    assert exc.category == outcome.ABSENT
    assert "Android reports toppan as not running" in str(exc)
    assert "crash" not in str(exc)


# --- the verifier's redirect: toppan hands over to a browser after Share -------------------------
#
# 2026-10-27 nightly, igrant_playground_pid_mdoc and pid_sdjwt: right after Share the current
# package was "com.android.chrome". The user decided this passes, without claiming `accept`.

def _share_then_front(phone, front):
    _verification(phone, request=[("request", None)], after_share=[("", None)])
    original = phone.tap

    def tap(name):
        original(name)
        if name == "Share":
            phone.shared_at = phone.clock
            phone.front = front
    phone.tap = tap


def test_a_handover_to_the_browser_after_share_passes_without_claiming_accept(phone):
    _share_then_front(phone, "com.android.chrome")
    _verify(phone)
    trail = progress.snapshot()
    assert trail["stage"] == progress.PRESENT
    assert trail["steps"][-1] == ("present: toppan handed over to com.android.chrome after Share "
                                  "(the verifier's redirect)")
    assert phone.clock - phone.shared_at < 5, "noticed within the poll, not after the timeout"
    assert "Back to Home Page" not in phone.taps


def test_another_app_in_front_after_share_is_never_a_pass(phone):
    _share_then_front(phone, "com.google.android.apps.nexuslauncher")
    exc, trail = _raised(_verify, phone)
    assert exc.category == outcome.PROCESSING
    assert not any("handed over" in s for s in trail["steps"])


def test_a_crash_dialog_wins_over_the_browser(phone):
    _share_then_front(phone, "com.android.chrome")
    phone.crash = SystemOverlay.APP_CRASH
    exc, _ = _raised(_verify, phone)
    assert exc.category == outcome.CRASHED


def test_a_dead_session_reading_the_foreground_after_share_is_ours(phone):
    _share_then_front(phone, DEAD)
    exc, _ = _raised(_verify, phone)
    assert exc.category == outcome.LOST_SESSION


# --- a tap whose button is gone ------------------------------------------------------------------
#
# `BasePage.click` raises exactly `Exception` when a button is not clickable in time. The screen
# had shown a moment before, so the flows look again and say what is there now.

def _failing_page(phone, method, exc, becomes):
    """A page whose tap fails with `exc` after the screen has changed to `becomes`."""
    class Page:
        def __init__(self, driver, **kw):
            pass

    def tap(self):
        # "dead": the session is gone by the time the flow looks at the screen again
        phone.show(*(becomes if becomes != "dead" else [("", None)]))
        phone.dead_after = len(phone.asked) if becomes == "dead" else None
        raise exc
    setattr(Page, method, tap)
    return Page


def _start_continue(phone):
    phone.show(("confirm", None))


def _start_issue_accept(phone):
    phone.show(("confirm", None))
    phone.then = {"CONTINUE": [("offer", None)]}


def _start_verify_share(phone):
    phone.show(("confirm", None))
    phone.then = {"CONTINUE": [("request", None)]}


# name -> (module, page class, method, screen the tap needs, how to start, run, stage)
TAPS = {
    "issue CONTINUE": (cf, "ConfirmationPage", "confirm", "confirm", _start_continue,
                       _issue, progress.OPEN),
    "issue Accept": (cf, "CredentialOfferPage", "accept", "offer", _start_issue_accept,
                     _issue, progress.PRESENT),
    "verify CONTINUE": (vf, "VerificationConfirmationPage", "confirm", "confirm",
                        _start_continue, _verify, progress.OPEN),
    "verify Share": (vf, "VerificationRequestPage", "share", "request", _start_verify_share,
                     _verify, progress.PRESENT),
}
tap_cases = pytest.mark.parametrize("tap", list(TAPS))


def _break_tap(phone, monkeypatch, tap, becomes, exc=None):
    module, page, method, screen, start, run, stage = TAPS[tap]
    exc = exc or Exception(f"Element ('xpath', '//*[@text=\"x\"]') not clickable after 10s")
    monkeypatch.setattr(module, page, _failing_page(phone, method, exc, becomes))
    start(phone)
    return run, screen, stage, exc


@tap_cases
def test_a_tap_that_finds_toppans_error_dialog_is_rejected_quoting_it(phone, monkeypatch, tap):
    run, _, stage, _ = _break_tap(phone, monkeypatch, tap, [("error", None)])
    exc, trail = _raised(run, phone)
    assert exc.category == outcome.REJECTED
    assert "Unsupported client_id prefix" in str(exc)
    assert "[no_retry]" in str(exc)
    assert trail["stage"] == stage


@tap_cases
def test_a_tap_that_finds_the_wallet_screen_is_dismissed(phone, monkeypatch, tap):
    run, _, stage, _ = _break_tap(phone, monkeypatch, tap, [("home", None)])
    exc, trail = _raised(run, phone)
    assert exc.category == outcome.DISMISSED
    assert "showed its wallet screen" in str(exc)
    assert "when checked 10s after the" in str(exc)
    assert trail["stage"] == stage


@tap_cases
def test_a_tap_that_finds_toppan_gone_says_it_left_not_that_it_never_came(phone, monkeypatch, tap):
    run, _, stage, _ = _break_tap(phone, monkeypatch, tap, [("", None)])
    phone.front = "com.android.chrome"
    exc, trail = _raised(run, phone)
    assert exc.category == outcome.ABSENT
    assert "had left the foreground" in str(exc)
    assert "did not come to the foreground" not in str(exc)
    assert trail["stage"] == stage


@tap_cases
def test_a_session_that_dies_while_the_screen_is_looked_at_again_is_ours(phone, monkeypatch, tap):
    run, _, stage, _ = _break_tap(phone, monkeypatch, tap, "dead")
    exc, trail = _raised(run, phone)
    assert exc.category == outcome.LOST_SESSION
    assert trail["stage"] == stage


@tap_cases
def test_a_tap_whose_screen_is_still_up_keeps_the_pages_own_error(phone, monkeypatch, tap):
    module, page, method, screen, *_ = TAPS[tap]
    # the screen the tap needed is still showing afterwards: only the button is missing
    run, _, _, original = _break_tap(phone, monkeypatch, tap, [(screen, None)])
    with pytest.raises(Exception) as raised:
        run(phone)
    assert raised.value is original
    assert not isinstance(raised.value, outcome.FlowFailure)


@tap_cases
def test_a_tap_failing_in_any_other_way_is_not_diagnosed(phone, monkeypatch, tap):
    """Only the page's own "not clickable" Exception is read as a vanished screen."""
    boom = TypeError("not a tap that timed out")
    run, _, _, _ = _break_tap(phone, monkeypatch, tap, [("home", None)], exc=boom)
    with pytest.raises(TypeError) as raised:
        run(phone)
    assert raised.value is boom


def test_a_share_that_finds_the_result_screen_is_ended_early_in_its_words(phone, monkeypatch):
    phone.result_text = NO_DOCTYPE
    run, _, _, _ = _break_tap(phone, monkeypatch, "verify Share", [("result", None)])
    exc, trail = _raised(run, phone)
    assert exc.category == outcome.NO_MATCH
    assert NO_DOCTYPE in str(exc)
    assert trail["stage"] == progress.PRESENT


@pytest.mark.parametrize("tap, when, stage", [
    ("verify Share", "after showing the request, when Share was tapped", progress.PRESENT),
    ("verify CONTINUE", "before showing the request", progress.OPEN),
])
def test_a_tap_that_finds_the_result_screen_says_where_it_ended(phone, monkeypatch, tap, when,
                                                                stage):
    phone.result_text = "Something else entirely"
    run, _, _, _ = _break_tap(phone, monkeypatch, tap, [("result", None)])
    exc, trail = _raised(run, phone)
    assert exc.category == outcome.REJECTED
    assert f"for 'pension_verification' {when}: \"Something else entirely\" [no_retry]" in str(exc)
    assert trail["stage"] == stage


@pytest.mark.parametrize("tap, when", [
    ("verify Share", "after showing the request, when Share was tapped"),
    ("verify CONTINUE", "before showing the request"),
])
def test_an_unreadable_result_screen_after_a_tap_says_where_it_ended(phone, monkeypatch, tap,
                                                                     when):
    run, _, _, _ = _break_tap(phone, monkeypatch, tap, [("result", None)])
    exc, _ = _raised(run, phone)
    assert exc.category == outcome.REJECTED
    assert f"on its result screen {when}; its message could not be read" in str(exc)


@pytest.mark.parametrize("tap", ["verify Share", "verify CONTINUE"])
def test_a_tap_that_finds_the_result_screen_without_a_request_is_no_match_after_either(
        phone, monkeypatch, tap):
    phone.result_text = NO_DOCTYPE
    run, _, _, _ = _break_tap(phone, monkeypatch, tap, [("result", None)])
    exc, _ = _raised(run, phone)
    assert exc.category == outcome.NO_MATCH


@tap_cases
def test_a_tap_that_finds_toppan_on_an_unrecognised_screen_is_processing(phone, monkeypatch, tap):
    run, _, stage, _ = _break_tap(phone, monkeypatch, tap, [("", None)])
    exc, trail = _raised(run, phone)
    assert exc.category == outcome.PROCESSING
    assert trail["stage"] == stage


@tap_cases
def test_a_tap_that_finds_a_crash_dialog_is_crashed(phone, monkeypatch, tap):
    run, _, stage, _ = _break_tap(phone, monkeypatch, tap, [("", None)])
    phone.crash = SystemOverlay.APP_CRASH
    exc, trail = _raised(run, phone)
    assert exc.category == outcome.CRASHED
    assert trail["stage"] == stage


def test_the_taps_look_again_for_the_default_timeout_after_their_screen(phone, monkeypatch):
    _break_tap(phone, monkeypatch, "issue Accept", [("home", None)])
    exc, _ = _raised(_issue, phone)
    assert "10s after the credential offer showed" in str(exc)


def test_the_pages_hold_no_second_wait_of_their_own():
    """The flows own the wait before each tap; the pages' own copy only ever fired in a race and
    said nothing about what had happened."""
    from wallets.toppan.pages import credential_offer_page, verification_request_page
    for module in (credential_offer_page, verification_request_page):
        source = inspect.getsource(module)
        assert "wait_until_loaded" not in source
        assert "did not appear within" not in source
        assert "WebDriverWait" not in source
