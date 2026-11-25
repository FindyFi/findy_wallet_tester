"""Every way paradym's flows can end, mapped to the outcome it raises and how far it got.

Before, every paradym branch raised a bare RuntimeError tagged with a phase ("[after consent]"),
its error screen was always `[no_retry]`, a refused deeplink escaped as a raw driver exception, its
"UNAVAILABLE CARDS" answer was untyped (the flow tapped Close before raising, yet all 41 nightly
no-match dumps still show the screen), and both flows treated "Go to wallet" as success although
paradym's error screen carries the same button.

Two layers, neither touching a device:

- the **page probes** run for real against copies of paradym's captured dumps, through a driver
  that evaluates their XPath with ElementTree;
- the **flows** run for real against `Phone`, which answers each probe from a scripted sequence
  of screens and keeps a virtual clock, so a 30-second wait costs nothing and every timeout the
  flow passes is visible.
"""
import re
import time
import xml.etree.ElementTree as ET
from types import SimpleNamespace

import pytest
from appium.webdriver.common.appiumby import AppiumBy
from selenium.common.exceptions import NoSuchElementException, WebDriverException

from base import deeplink, flow_failures, outcome, progress
from base.android import SystemOverlay
from base.conftest_helpers import _QUOTED
from base.flow_context import FlowContext
from base.screens import UNKNOWN
from wallets.paradym.flows import _failures as failures
from wallets.paradym.flows import credential_flow as cf, init_flow, verification_flow as vf
from wallets.paradym.pages import error_page, result_page, verification_request_page
from wallets.paradym.screens import SCREENS

PACKAGE = "id.paradym.wallet"
URL = "openid-credential-offer://?credential_offer_uri=https%3A%2F%2Fparadym.id%2Finvitation"
PAGE_ARGS = {"timeouts": {"default": 10, "credential_offer": 30}}

REFUSED = ("Error attempting to start URI. Original error: Error: Starting: Intent "
           "{ act=android.intent.action.VIEW dat=openid4vp://... pkg=" + PACKAGE + " }\n"
           "Error: Activity not started, unable to resolve Intent "
           "{ act=android.intent.action.VIEW dat=openid4vp://... flg=0x10000000 "
           "pkg=" + PACKAGE + " }")
DEAD = WebDriverException("instrumentation process is not running")

# paradym's own Reasons, from the dumps. With Development Mode on (nightly 2026-10-27, waltid) and
# off (reports/2026-08-27_18-46-59, authbound_issuer), and a verifier's JSON body (2026-10-27).
WALTID_ISSUER = ("Reason: Error while retrieving credentials\n\nDevelopment mode error:\n"
                 "Credential configuration 'PensionCredential_vc+sd-jwt' does not specify "
                 "proof_types_supported. Credentials not bound to keys are not supported at the "
                 "moment")
NO_DEV_MODE = "Reason: Error while retrieving credentials"
WALTID_VERIFIER = ("Reason: Presentation information could not be extracted\nDevelopment mode "
                   "error: When the client identifier prefix is 'redirect_uri', the client id "
                   "identifier MUST match the response_uri.\n{\n  \"error\": \"invalid_client\",\n"
                   "  \"error_description\": \"When the client identifier prefix is "
                   "'redirect_uri', the client id identifier MUST match the response_uri.\"\n}")


def wallet_said(exc) -> str:
    """What the app.log digest would publish as the wallet's words: the first quoted span."""
    m = _QUOTED.search(str(exc))
    return m.group(1) if m else ""


# --- captured screens ----------------------------------------------------------------------------
#
# Structure and attributes copied from the dumps (layout wrappers and SVG nodes dropped). The error
# screen: PUBLISH_findy_wallet_tester/reports/2026-10-27_22-00-02/paradym/xml_dumps/
# test_credential_issuance_waltid_issuer_credential_issuance-paradym.xml. The no-match review
# screen: …/2026-10-14_22-00-01/…/test_credential_verification_procivis_verifier_…-paradym.xml.
# Home: …/2026-09-30_22-00-03/…/test_credential_issuance_hovi_issuer_…-paradym.xml.

def _esc(text):
    return (text.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;")
            .replace("\n", "&#10;"))


def _paradym(body):
    return (f'<hierarchy><android.widget.FrameLayout package="{PACKAGE}">'
            f'<android.widget.Button content-desc="Back" clickable="true" package="{PACKAGE}"/>'
            f'<android.widget.Button content-desc="Cancel" clickable="true" package="{PACKAGE}"/>'
            f'{body}</android.widget.FrameLayout></hierarchy>')


def _error(reason, sentence="An error occurred while fetching the card information. Ask the "
                            "issuer to generate a new QR-code or try again later."):
    p = f'package="{PACKAGE}"'
    return _paradym(
        f'<android.view.View text="Something went wrong" heading="true" {p}/>'
        f'<android.widget.TextView text="{_esc(sentence)}" {p}/>'
        f'<android.widget.TextView text="{_esc(reason)}" {p}/>'
        f'<android.widget.Button content-desc="Go to wallet,  " clickable="true" {p}>'
        f'<android.widget.TextView text="Go to wallet" {p}/>'
        f'<android.widget.TextView text=" " {p}/></android.widget.Button>'
    )


NO_MATCH = _paradym(
    f'<android.view.View text="Review the request" heading="true" package="{PACKAGE}"/>'
    f'<android.view.View text="UNAVAILABLE CARDS" heading="true" package="{PACKAGE}"/>'
    f'<android.widget.TextView text="You don&apos;t have the requested card(s)." '
    f'package="{PACKAGE}"/>'
    f'<android.widget.TextView text="Hetu" package="{PACKAGE}"/>'
    f'<android.widget.TextView text="You don&apos;t have the required cards" package="{PACKAGE}"/>'
    f'<android.widget.Button content-desc="Close" clickable="true" package="{PACKAGE}">'
    f'<android.widget.TextView text="Close" package="{PACKAGE}"/></android.widget.Button>'
)

HOME = _paradym(
    f'<android.view.View text="Hello!" heading="true" package="{PACKAGE}"/>'
    f'<android.widget.Button content-desc="All cards. 1 card total" clickable="true" '
    f'package="{PACKAGE}"/>'
    f'<android.widget.TextView text="1 card total" package="{PACKAGE}"/>'
)


class _Node:
    def __init__(self, screen, node):
        self._screen, self._node = screen, node

    @property
    def text(self):
        return self._node.get("text", "")

    def get_attribute(self, name):
        return self._node.get(name, "")

    def click(self):
        self._screen.tapped.append(self._node.get("text") or self._node.get("content-desc", ""))


# The two XPath functions paradym's locators use, which ElementTree does not have. Anything else
# goes to ElementTree, so a locator in a shape neither understands fails here.
_FUNCTION = re.compile(r'//\*\[(starts-with|contains)\(@([\w-]+), "([^"]*)"\)\]')


class Screen:
    """One captured dump as a driver."""
    current_package = PACKAGE
    capabilities: dict = {}

    def __init__(self, xml):
        self.root = ET.fromstring(xml)
        self.tapped = []

    def find_elements(self, by, value):
        nodes = list(self.root.iter())
        m = _FUNCTION.fullmatch(value) if by == AppiumBy.XPATH else None
        if m:
            fn, attr, arg = m.groups()
            test = str.startswith if fn == "starts-with" else str.__contains__
            found = [n for n in nodes if test(n.get(attr, ""), arg)]
        elif by == AppiumBy.ACCESSIBILITY_ID:
            found = [n for n in nodes if n.get("content-desc") == value]
        else:
            assert by == AppiumBy.XPATH and value.startswith("//"), value
            # `[@a="x" and @b="y"]` (home's heading) means `[@a="x"][@b="y"]`, which ElementTree
            # reads.
            found = self.root.findall("." + value.replace('" and @', '"][@'))
        return [_Node(self, n) for n in found]

    def find_element(self, by, value):
        found = self.find_elements(by, value)
        if not found:
            raise NoSuchElementException(value)
        return found[0]


# --- the page probes, against the captured dumps -------------------------------------------------

def test_the_error_probe_matches_paradyms_error_screen_and_nothing_else():
    assert error_page.present(Screen(_error(WALTID_ISSUER)), timeout=0.1)
    assert not error_page.present(Screen(NO_MATCH), timeout=0.1)
    assert not error_page.present(Screen(HOME), timeout=0.1)


def test_an_error_heading_without_its_heading_attribute_is_still_an_error():
    """Success is Go to wallet *without* this heading: a probe blinded by a missing attribute
    would pass every error after Accept or Share."""
    xml = _error(WALTID_ISSUER).replace(' heading="true"', "")
    assert 'heading="true"' not in xml
    assert error_page.present(Screen(xml), timeout=0.1)


def test_the_reason_is_paradyms_verdict_with_the_development_mode_detail_apart():
    said, detail = error_page.split_reason(error_page.raw_reason(Screen(_error(WALTID_ISSUER))))
    assert said == "Error while retrieving credentials"
    assert detail.startswith("Credential configuration 'PensionCredential_vc+sd-jwt' does not")
    assert "\n" not in detail


def test_a_reason_without_development_mode_is_the_verdict_alone():
    screen = Screen(_error(NO_DEV_MODE))
    assert error_page.message(screen) == "Error while retrieving credentials"
    assert error_page.split_reason(error_page.raw_reason(screen))[1] == ""


def test_the_detail_cannot_pair_its_quotes_with_ours_and_is_cut_to_a_readable_length():
    said, detail = error_page.split_reason(WALTID_VERIFIER)
    assert said == "Presentation information could not be extracted"
    assert '"' not in detail and "'invalid_client'" in detail
    _, long = error_page.split_reason(NO_DEV_MODE + "\n\nDevelopment mode error:\n" + "x" * 5000)
    assert len(long) == error_page._DETAIL_LIMIT and long.endswith("…")


def test_reading_the_reason_never_raises():
    class Broken:
        def find_elements(self, *a):
            raise WebDriverException("gone")
    assert error_page.message(Broken()) == ""
    assert error_page.message(Screen(HOME)) == ""


def test_go_to_wallet_is_on_the_error_screen_too_so_it_alone_is_not_success():
    assert result_page.go_to_wallet_on_screen(Screen(_error(WALTID_ISSUER)), timeout=0.1)
    assert not result_page.go_to_wallet_on_screen(Screen(NO_MATCH), timeout=0.1)


def test_a_go_to_wallet_gone_before_the_tap_is_not_a_lost_session():
    """NoSuchElementException is a WebDriverException: unhandled, the flows would have called a
    missing button a dead session."""
    error = Screen(_error(WALTID_ISSUER))
    assert result_page.go_to_wallet(error) and error.tapped == ["Go to wallet,  "]
    assert result_page.go_to_wallet(Screen(NO_MATCH)) is False


def test_the_no_match_probe_reads_paradyms_unavailable_cards_answer():
    assert verification_request_page.no_match(Screen(NO_MATCH), timeout=0.1)
    assert not verification_request_page.no_match(Screen(_error(WALTID_VERIFIER)), timeout=0.1)
    assert not verification_request_page.on_screen(Screen(NO_MATCH), timeout=0.1), "no Share"


def test_teardown_closes_the_no_match_screen_through_its_own_close_and_nothing_else():
    screen = Screen(NO_MATCH)
    assert verification_request_page.close_no_match(screen)
    assert screen.tapped == ["Close"]
    other = Screen(_error(WALTID_ISSUER))
    assert not verification_request_page.close_no_match(other)
    assert other.tapped == []


def test_screens_declares_only_what_a_dump_shows():
    error = Screen(_error(WALTID_ISSUER))
    assert SCREENS.error(error, 0.1) and not SCREENS.error(Screen(HOME), 0.1)
    assert SCREENS.error_text(error) == "Error while retrieving credentials"
    assert SCREENS.home(Screen(HOME), 0.1) and not SCREENS.home(error, 0.1)
    assert SCREENS.no_match(Screen(NO_MATCH), 0.1)
    assert SCREENS.processing is UNKNOWN and SCREENS.success is UNKNOWN


def test_teardown_recognises_the_no_match_screen_with_one_lookup_and_no_wait(monkeypatch):
    """Every other probe in `_detect_state` waits 2 s; five of them stood between teardown and
    the Close."""
    waited = []
    monkeypatch.setattr(init_flow, "wait_present", lambda *a, **k: waited.append(a) or False)
    monkeypatch.setattr(init_flow.pin_screen, "on_screen", lambda *a, **k: False)
    assert init_flow._detect_state(Screen(NO_MATCH)) == "no_match"
    assert waited == []
    assert init_flow._detect_state(Screen(HOME)) == "unknown"
    assert waited, "any other screen goes on to the usual probes"


def test_teardown_recognises_the_go_to_wallet_screen_first_with_no_wait(monkeypatch):
    """The error screen carries Go to wallet; it was probed last, behind four 2 s probes."""
    waited, looked = [], []
    monkeypatch.setattr(init_flow, "wait_present", lambda *a, **k: waited.append(a) or False)
    monkeypatch.setattr(init_flow.pin_screen, "on_screen",
                        lambda *a, **k: looked.append(a) or False)
    assert init_flow._detect_state(Screen(_error(WALTID_ISSUER))) == "credential_success"
    assert waited == [] and looked == []


def test_the_go_to_wallet_lookup_never_waits_even_where_the_button_is_absent(monkeypatch):
    """Teardown runs it on every screen; a wait there would cost every non-error screen too."""
    waited = []
    monkeypatch.setattr(result_page, "wait_present", lambda *a, **k: waited.append(a) or False)
    assert result_page.go_to_wallet_now(Screen(_error(WALTID_ISSUER)))
    assert not result_page.go_to_wallet_now(Screen(HOME))
    assert waited == []


def test_teardown_closes_the_no_match_screen_and_carries_on_from_home(monkeypatch):
    states = ["no_match", "home"]
    calls = []
    monkeypatch.setattr(init_flow, "_detect_state", lambda d: states.pop(0))
    monkeypatch.setattr(init_flow, "close_no_match", lambda d: calls.append("close") or True)
    monkeypatch.setattr(init_flow, "_back_to_known_state",
                        lambda d, p: pytest.fail("no back presses for a screen with a Close"))
    init_flow.run(SimpleNamespace(current_package=PACKAGE), pin="123456", app_package=PACKAGE)
    assert calls == ["close"] and states == []


def test_the_back_press_loop_closes_a_no_match_screen_rather_than_calling_it_known(monkeypatch):
    states = ["no_match", "home"]
    calls = []
    monkeypatch.setattr(init_flow, "_dismiss_permission_dialog", lambda d: None)
    monkeypatch.setattr(init_flow, "_tap_go_to_wallet", lambda d: None)
    monkeypatch.setattr(init_flow, "_detect_state", lambda d: states.pop(0))
    monkeypatch.setattr(init_flow, "close_no_match", lambda d: calls.append("close") or True)
    driver = SimpleNamespace(current_package=PACKAGE, back=lambda: calls.append("back"))
    assert init_flow._back_to_known_state(driver, PACKAGE) == "home"
    assert calls == ["close"]


# --- paradym, scripted, on a virtual clock -------------------------------------------------------

# A screen that also satisfies other probes: paradym's error screen carries Go to wallet.
_ALSO = {"error": {"success"}}


class Phone:
    """What paradym shows, as a driver whose probes are answered from a script.

    `show(...)` sets what is on screen now, and `then` maps a tap to what follows it. Either is a
    list of (screen, length) segments, the last of which stays: an int length is a number of looks
    by any probe, a float one is seconds on the clock since the screens were shown. A look at a
    screen that is not there advances the clock by the probe's timeout, as `wait_present` would;
    one that is there costs nothing.
    """
    capabilities: dict = {}

    def __init__(self):
        self.clock = 1000.0
        self.front = PACKAGE
        self.app_state = 3
        self.refuse = None
        self.dead_after = None
        self.crash = None
        self.reason = WALTID_ISSUER
        self.then = {}
        self.asked, self.taps = [], []
        self.show(("", None))

    @property
    def current_package(self):
        if self.dead_after is not None and len(self.asked) > self.dead_after:
            raise DEAD
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
        def look(driver, timeout=2):
            self.asked.append((name, timeout))
            assert len(self.asked) < 5000, "the flow is looping instead of reaching a verdict"
            if self.dead_after is not None and len(self.asked) > self.dead_after:
                raise DEAD
            on = self.screen()
            shown = on == name or name in _ALSO.get(on, ())
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
        setattr(Page, method, lambda self, *a, label=label: phone.tap(label))
    return Page


@pytest.fixture
def phone(monkeypatch):
    p = Phone()
    progress.reset()
    monkeypatch.setattr(time, "monotonic", p.now)
    monkeypatch.setattr(error_page, "present", p.probe("error"))
    monkeypatch.setattr(error_page, "raw_reason", lambda d: p.reason)
    monkeypatch.setattr(result_page, "go_to_wallet_on_screen", p.probe("success"))
    monkeypatch.setattr(result_page, "go_to_wallet", lambda d: p.tap("Go to wallet") or True)
    monkeypatch.setattr(failures, "home_on_screen", p.probe("home"))
    monkeypatch.setattr(flow_failures, "handle_permission_if_present", lambda d: False)
    monkeypatch.setattr(flow_failures, "detect_crash_or_anr", lambda d, *a, **k: p.crash)
    monkeypatch.setattr(failures, "PinPage", _page(p, enter_pin="PIN"))

    monkeypatch.setattr(cf, "pin_on_screen", p.probe("pin"))
    monkeypatch.setattr(cf, "consent_on_screen", p.probe("consent"))
    monkeypatch.setattr(cf, "authenticate_on_screen", p.probe("authenticate"))
    monkeypatch.setattr(cf, "card_offered_on_screen", p.probe("card"))
    monkeypatch.setattr(cf, "tx_code_on_screen", p.probe("tx"))
    monkeypatch.setattr(cf, "offer_on_screen", p.probe("offer"))
    monkeypatch.setattr(cf, "fetching_on_screen", p.probe("fetching"))
    monkeypatch.setattr(cf, "IssuerConsentPage", _page(p, confirm="Yes, continue"))
    monkeypatch.setattr(cf, "AuthenticatePage", _page(p, authenticate="Authenticate"))
    monkeypatch.setattr(cf, "CardOfferedPage", _page(p, continue_="Continue"))
    monkeypatch.setattr(cf, "TxCodePage", _page(p, enter_code="tx code"))
    monkeypatch.setattr(cf, "CredentialOfferPage", _page(p, accept="Accept"))

    monkeypatch.setattr(vf, "pin_on_screen", p.probe("pin"))
    monkeypatch.setattr(vf, "consent_on_screen", p.probe("consent"))
    monkeypatch.setattr(vf, "no_match_on_screen", p.probe("no_match"))
    monkeypatch.setattr(vf, "request_on_screen", p.probe("request"))
    monkeypatch.setattr(vf, "IssuerConsentPage", _page(p, confirm="Yes, continue"))
    monkeypatch.setattr(vf, "VerificationRequestPage", _page(p, share="Share"))

    monkeypatch.setattr(deeplink, "unroutable_reason", lambda *a, **k: "")
    monkeypatch.setattr(deeplink, "registered_schemes", lambda *a, **k: [])
    monkeypatch.setattr(outcome, "detect_crash_or_anr", lambda *a, **k: None)
    yield p
    progress.reset()


def _issue(phone, tx_code=None):
    cf.run(phone, provider=SimpleNamespace(get=lambda n: URL, last_tx_code=tx_code),
           credential_name="credential_issuance", app_package=PACKAGE, pin="123456", **PAGE_ARGS)


def _verify(phone, pin="123456"):
    vf.run(phone, provider=SimpleNamespace(get=lambda n: URL),
           credential_name="pension_verification", app_package=PACKAGE, pin=pin, **PAGE_ARGS)


def _raised(fn, *args, **kw):
    with pytest.raises(outcome.FlowFailure) as exc:
        fn(*args, **kw)
    return exc.value, progress.snapshot()


def _issuance_until_accept(phone, after_accept):
    """The passing runs' order (nightly 2026-10-27 test.log): PIN, consent, Card offered, offer."""
    phone.show(("pin", None))
    phone.then = {"PIN": [("consent", None)], "Yes, continue": [("card", None)],
                  "Continue": [("offer", None)], "Accept": after_accept}


def _verification_until_share(phone, after_share):
    phone.show(("pin", None))
    phone.then = {"PIN": [("consent", None)], "Yes, continue": [("request", None)],
                  "Share": after_share}


def _steps(trail):
    return [s.split(": ", 1)[1] for s in trail["steps"]]


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


def test_a_stored_credential_completes_accept_and_leaves_keep_to_the_count(phone):
    _issuance_until_accept(phone, [("success", None)])
    _issue(phone)
    trail = progress.snapshot()
    assert phone.taps == ["PIN", "Yes, continue", "Continue", "Accept", "Go to wallet"]
    assert trail["stage"] == progress.ACCEPT
    assert "tapped Accept" in _steps(trail)
    assert "entered the wallet PIN after the deeplink" in _steps(trail)
    assert "confirmed the issuer consent" in _steps(trail)


def test_an_error_straight_after_the_pin_is_rejected_at_open_quoting_paradym(phone):
    """procivis_issuer, 52 nights: "Credential information could not be extracted" after PIN."""
    phone.reason = ("Reason: Credential information could not be extracted\n\nDevelopment mode "
                    "error:\nError: Fetching well known metadata from 'https://x' resulted in an "
                    "unsuccessful response with status '400'.\nHeader of type `accept` was missing")
    phone.show(("pin", None))
    phone.then = {"PIN": [("error", None)]}
    exc, trail = _raised(_issue, phone)
    assert exc.category == outcome.REJECTED
    assert str(exc).startswith("[rejected] [credential_flow] paradym could not process the "
                               "credential offer for 'credential_issuance': \"Credential "
                               "information could not be extracted\". paradym's development-mode "
                               "detail: Error: Fetching well known metadata")
    assert wallet_said(exc) == "Credential information could not be extracted"
    assert "\n" not in str(exc)
    assert str(exc).endswith("[no_retry]")
    assert trail["stage"] == progress.OPEN
    assert _steps(trail)[-2:] == ["entered the wallet PIN after the deeplink",
                                  "paradym was in front, showing its error screen"]
    assert phone.clock - 1000 < 5, "the error ends the wait; it is not found at the end"


def test_an_error_after_the_consent_names_the_step_it_came_after(phone):
    """waltid_issuer, 46 nights: "Error while retrieving credentials" after the consent."""
    phone.show(("pin", None))
    phone.then = {"PIN": [("consent", None)], "Yes, continue": [("error", None)]}
    exc, trail = _raised(_issue, phone)
    assert exc.category == outcome.REJECTED
    assert wallet_said(exc) == "Error while retrieving credentials"
    assert trail["stage"] == progress.OPEN
    assert _steps(trail)[-2] == "confirmed the issuer consent"


def test_an_error_after_accept_is_rejected_after_the_offer(phone):
    _issuance_until_accept(phone, [("", 3), ("error", None)])
    exc, trail = _raised(_issue, phone)
    assert exc.category == outcome.REJECTED
    assert "took the Accept for 'credential_issuance' and then failed" in str(exc)
    assert trail["stage"] == progress.PRESENT
    assert "Go to wallet" not in phone.taps, "the error screen stays up for the screenshot"


def test_go_to_wallet_with_the_error_heading_beside_it_is_not_success(phone):
    """The error screen renders between the heading probe and the button probe: the last look
    catches it, where the old flow would have tapped Go to wallet as a success."""
    _issuance_until_accept(phone, [("", 1), ("error", None)])
    exc, trail = _raised(_issue, phone)
    assert exc.category == outcome.REJECTED
    assert trail["stage"] == progress.PRESENT
    assert phone.waited("error")[-1] == failures._LAST_ERROR_LOOK


@pytest.mark.parametrize("reason", [
    "Reason: Error while retrieving credentials\n\nDevelopment mode error:\nUnable to retrieve "
    "access token from 'https://x/token'. Received token error response with status 502",
    "Reason: Credential information could not be extracted\n\nDevelopment mode error:\nError: "
    "Fetching credential offer from 'https://x' resulted in an unsuccessful response with status "
    "'503'.",
], ids=["status-502", "status-'503'"])
def test_a_gateway_failure_is_rejected_but_may_be_retried(phone, reason):
    phone.reason = reason
    phone.show(("error", None))
    exc, _ = _raised(_issue, phone)
    assert exc.category == outcome.REJECTED
    assert "[no_retry]" not in str(exc)


def test_an_offer_the_issuer_no_longer_has_may_be_retried(phone):
    """hovi_issuer, reports/2026-09-03_11-49-09 and 11-52-14; the 11-54-16 run passed."""
    phone.reason = ("Reason: Credential information could not be extracted\n\nDevelopment mode "
                    "error:\nError: Fetching credential offer from 'https://core-agent.hovi.id/"
                    "oid4vci/f50cefc7/offers/f403dfaf' resulted in an unsuccessful response with "
                    "status '404'")
    phone.show(("error", None))
    exc, _ = _raised(_issue, phone)
    assert exc.category == outcome.REJECTED
    assert wallet_said(exc) == "Credential information could not be extracted"
    assert "[no_retry]" not in str(exc)


# The phone's network on 2026-10-29 (reports/2026-10-29_10-36-12), the one shape per flow.
UNREACHABLE_ISSUER = (
    "Reason: Credential information could not be extracted\n\nDevelopment mode error:\nUnknown "
    "error occurred during fetch to 'https://paradym.id/invitation/7852b540/offers/0'\nCause: "
    "fetch failed: java.net.UnknownHostException: Unable to resolve host \"paradym.id\": No "
    "address associated with hostname")
UNREACHABLE_VERIFIER = (
    "Reason: Presentation information could not be extracted\nDevelopment mode error: Fetching "
    "request_object from request_uri 'https://core-agent.hovi.id/oid4vp/90991e13/authorization-"
    "requests/119005f2' failed\n{\n  \"error_description\": \"Fetching request_object from "
    "request_uri 'https://core-agent.hovi.id/oid4vp/90991e13/authorization-requests/119005f2' "
    "failed\",\n  \"error\": \"invalid_request_uri\"\n}")


def test_an_issuer_paradym_could_not_reach_is_rejected_but_may_be_retried(phone):
    phone.reason = UNREACHABLE_ISSUER
    phone.show(("error", None))
    exc, _ = _raised(_issue, phone)
    assert exc.category == outcome.REJECTED
    assert wallet_said(exc) == "Credential information could not be extracted"
    assert "[no_retry]" not in str(exc)


def test_a_request_object_paradym_could_not_fetch_is_rejected_but_may_be_retried(phone):
    phone.reason = UNREACHABLE_VERIFIER
    phone.show(("error", None))
    exc, _ = _raised(_verify, phone)
    assert exc.category == outcome.REJECTED
    assert wallet_said(exc) == "Presentation information could not be extracted"
    assert "[no_retry]" not in str(exc)


def test_paradyms_fetch_wrapper_alone_is_enough_without_its_cause(phone):
    phone.reason = UNREACHABLE_ISSUER.split("\nCause:")[0]
    phone.show(("error", None))
    exc, _ = _raised(_issue, phone)
    assert "[no_retry]" not in str(exc)


def test_the_host_lookup_alone_decides_even_past_the_message_cut(phone):
    phone.reason = (UNREACHABLE_ISSUER.replace("Unknown error occurred during fetch",
                                               "x" * 400))
    phone.show(("error", None))
    exc, _ = _raised(_issue, phone)
    assert "UnknownHostException" not in str(exc), "cut from the message"
    assert "[no_retry]" not in str(exc)


def test_waltids_verdict_on_the_same_outage_night_keeps_no_retry(phone):
    """waltid_verifier failed its usual way on 2026-10-29 too: a verdict, not the network."""
    phone.reason = WALTID_VERIFIER
    phone.show(("error", None))
    exc, _ = _raised(_verify, phone)
    assert str(exc).endswith("[no_retry]")


def test_a_404_on_anything_but_the_offer_is_still_a_verdict(phone):
    phone.reason = ("Reason: Credential information could not be extracted\n\nDevelopment mode "
                    "error:\nError: Fetching well known metadata from 'https://x' resulted in an "
                    "unsuccessful response with status '404'.")
    phone.show(("error", None))
    exc, _ = _raised(_issue, phone)
    assert str(exc).endswith("[no_retry]")


def test_a_status_past_the_message_cut_still_decides_the_retry(phone):
    phone.reason = ("Reason: Error while retrieving credentials\n\nDevelopment mode error:\n"
                    + "x" * 400 + " Received token error response with status 503")
    phone.show(("error", None))
    exc, _ = _raised(_issue, phone)
    assert "status 503" not in str(exc), "cut from the message"
    assert "[no_retry]" not in str(exc)


def test_a_client_error_status_is_still_a_verdict(phone):
    phone.reason = ("Reason: Error while retrieving credentials\n\nDevelopment mode error:\n"
                    "Received token error response with status 400")
    phone.show(("error", None))
    exc, _ = _raised(_issue, phone)
    assert str(exc).endswith("[no_retry]")


def test_an_unreadable_reason_quotes_nothing_and_may_be_retried(phone):
    phone.reason = ""
    phone.show(("error", None))
    exc, _ = _raised(_issue, phone)
    assert exc.category == outcome.REJECTED
    assert wallet_said(exc) == ""
    assert "its Reason could not be read" in str(exc)
    assert "[no_retry]" not in str(exc)


def test_a_reason_without_development_mode_adds_no_detail(phone):
    phone.reason = NO_DEV_MODE
    phone.show(("error", None))
    exc, _ = _raised(_issue, phone)
    assert str(exc).endswith("\"Error while retrieving credentials\" [no_retry]")


@pytest.fixture
def warned_once(monkeypatch):
    monkeypatch.setattr(failures, "_warned_no_dev_mode", False)


def _dev_mode_warnings(caplog):
    return [r for r in caplog.records if "Development Mode looks off" in r.getMessage()]


def test_a_missing_development_mode_detail_is_warned_about_once_per_process(
        phone, warned_once, caplog):
    phone.reason = NO_DEV_MODE
    for _ in range(2):
        phone.show(("error", None))
        exc, _ = _raised(_issue, phone)
        assert "Development Mode" not in str(exc)
        assert str(exc).endswith("\"Error while retrieving credentials\" [no_retry]")
    assert len(_dev_mode_warnings(caplog)) == 1


def test_a_reason_with_its_detail_does_not_warn_about_development_mode(
        phone, warned_once, caplog):
    phone.reason = WALTID_ISSUER
    phone.show(("error", None))
    _raised(_issue, phone)
    assert _dev_mode_warnings(caplog) == []


def test_an_unreadable_reason_does_not_warn_about_development_mode(phone, warned_once, caplog):
    phone.reason = ""
    phone.show(("error", None))
    _raised(_issue, phone)
    assert _dev_mode_warnings(caplog) == []


def test_nothing_after_accept_is_no_verdict_rather_than_nothing_appeared(phone):
    _issuance_until_accept(phone, [("", None)])
    exc, trail = _raised(_issue, phone)
    assert exc.category == outcome.PROCESSING
    assert "neither its Go to wallet nor its error screen in the 30s after Accept" in str(exc)
    assert trail["stage"] == progress.PRESENT


def test_a_crash_after_accept_is_crashed_not_processing(phone):
    _issuance_until_accept(phone, [("", None)])
    phone.crash = SystemOverlay.ANR
    exc, _ = _raised(_issue, phone)
    assert exc.category == outcome.CRASHED
    assert "paradym stopped responding after Accept" in str(exc)


def test_a_success_answered_after_the_wait_still_counts_and_costs_about_a_second(phone):
    _issuance_until_accept(phone, [("", 30.0), ("success", None)])
    start = {}
    original = phone.tap

    def tap(name):
        original(name)
        if name == "Accept":
            start["at"] = phone.clock
    phone.tap = tap
    _issue(phone)
    assert progress.snapshot()["stage"] == progress.ACCEPT
    # 30 s of waiting, the late look of at most ~1 s, the 1 s last error look, and one pass of
    # the wait that overran the deadline.
    assert phone.clock - start["at"] < 30 + 1.5 + failures._LAST_ERROR_LOOK + 1.5


def test_a_dead_session_after_accept_is_ours(phone):
    _issuance_until_accept(phone, [("", None)])
    phone.dead_after = 20
    exc, _ = _raised(_issue, phone)
    assert exc.category == outcome.LOST_SESSION


def test_a_pin_screen_without_a_configured_pin_is_ours(phone):
    phone.show(("pin", None))
    exc, trail = _raised(cf.run, phone, provider=SimpleNamespace(get=lambda n: URL),
                         credential_name="credential_issuance", app_package=PACKAGE, pin="",
                         **PAGE_ARGS)
    assert exc.category == outcome.SETUP
    assert "pass pin= to credential_flow.run()" in str(exc)
    assert phone.taps == [], "no PIN is typed when none is configured"
    assert trail["stage"] == progress.OPEN


def test_no_offer_on_the_home_screen_is_dismissed_at_open(phone):
    phone.show(("home", None))
    exc, trail = _raised(_issue, phone)
    assert exc.category == outcome.DISMISSED
    assert "when checked 30s after the deeplink" in str(exc)
    assert trail["stage"] == progress.OPEN
    assert 30 <= phone.clock - 1000 < 36


def test_paradym_still_fetching_is_processing(phone):
    phone.show(("pin", None))
    phone.then = {"PIN": [("fetching", None)]}
    exc, trail = _raised(_issue, phone)
    assert exc.category == outcome.PROCESSING
    assert "still fetching when checked 30s after the PIN" in str(exc)
    assert trail["stage"] == progress.OPEN


def test_no_offer_with_paradym_elsewhere_is_the_shared_absent_diagnosis(phone):
    phone.front = "com.android.chrome"
    exc, trail = _raised(_issue, phone)
    assert exc.category == outcome.ABSENT
    assert "no credential offer, no error and no home screen" in str(exc)
    assert trail["stage"] == progress.DELIVER


def test_no_offer_with_paradym_in_front_on_an_unknown_screen_is_processing(phone):
    exc, trail = _raised(_issue, phone)
    assert exc.category == outcome.PROCESSING
    assert "No processing/success screen is mapped for paradym" in str(exc)
    assert trail["stage"] == progress.OPEN


def test_paradym_gone_after_the_consent_has_left_rather_than_never_come_forward(phone):
    phone.show(("pin", None))
    phone.then = {"PIN": [("consent", None)], "Yes, continue": [("", None)]}
    original = phone.tap

    def tap(name):
        original(name)
        if name == "Yes, continue":
            phone.front = "com.google.android.apps.nexuslauncher"
    phone.tap = tap
    exc, _ = _raised(_issue, phone)
    assert exc.category == outcome.ABSENT
    assert "had left the foreground when checked 30s after the consent" in str(exc)
    assert "did not come to the foreground" not in str(exc)


def test_a_crash_before_the_offer_is_crashed(phone):
    phone.crash = SystemOverlay.APP_CRASH
    exc, _ = _raised(_issue, phone)
    assert exc.category == outcome.CRASHED
    assert "paradym crashed after the deeplink" in str(exc)


def test_a_late_offer_continues_the_flow(phone):
    # Absent for the whole wait — its last pass ends at 31.5 s, having looked for the offer at
    # 30.5 s — and there for the re-probe after it.
    phone.show(("", 32.0), ("offer", None))
    phone.then = {"Accept": [("success", None)]}
    _issue(phone)
    assert phone.taps == ["Accept", "Go to wallet"]
    assert "paradym showed the credential offer late" in _steps(progress.snapshot())


def test_a_pin_screen_that_lingers_after_entry_is_not_answered_twice(phone):
    _issuance_until_accept(phone, [("success", None)])
    phone.then["PIN"] = [("pin", 3), ("consent", None)]
    _issue(phone)
    assert phone.taps == ["PIN", "Yes, continue", "Continue", "Accept", "Go to wallet"]


def test_a_pin_screen_still_up_after_entry_is_the_configured_pin_not_unrecognised(phone):
    phone.show(("pin", None))
    exc, trail = _raised(_issue, phone)
    assert exc.category == outcome.SETUP
    assert "showing its PIN screen again 30s after the PIN" in str(exc)
    assert "recognises" not in str(exc)
    assert phone.taps == ["PIN"]


def test_a_consent_still_up_after_it_was_answered_is_a_prompt_loop(phone):
    phone.show(("pin", None))
    phone.then = {"PIN": [("consent", None)]}
    exc, _ = _raised(_verify, phone)
    assert exc.category == outcome.PROMPT_LOOP
    assert "showing its verifier consent again 30s after the consent" in str(exc)
    assert phone.taps == ["PIN", "Yes, continue"]


def test_the_late_look_shares_one_second_between_its_targets(phone):
    asked = []

    def probe(driver, timeout):
        asked.append(timeout)
        return False
    ctx = SimpleNamespace(flow="credential_flow", what="x")
    screens = [(name, probe, 1.0) for name in ("a", "b", "c", "d")]
    assert failures.late_look(phone, ctx, screens, during="after x") == ""
    assert sum(asked) == pytest.approx(1.0) and len(asked) == 4


def test_an_error_screen_that_comes_after_the_wait_is_still_rejected(phone):
    phone.show(("", 32.0), ("error", None))
    exc, trail = _raised(_issue, phone)
    assert exc.category == outcome.REJECTED
    assert trail["stage"] == progress.OPEN


def test_the_transaction_code_is_probed_only_when_the_provider_has_one(phone):
    _issuance_until_accept(phone, [("success", None)])
    _issue(phone)
    assert phone.waited("tx") == []
    phone.taps.clear()
    progress.reset()
    phone.show(("tx", None))
    phone.then = {"tx code": [("offer", None)], "Accept": [("success", None)]}
    _issue(phone, tx_code="1234")
    assert phone.taps == ["tx code", "Accept", "Go to wallet"]


def test_every_wait_for_the_next_screen_gets_the_offer_timeout(phone):
    phone.show(("pin", None))
    phone.then = {"PIN": [("", 25.0), ("consent", None)], "Yes, continue": [("", 25.0),
                                                                            ("offer", None)],
                  "Accept": [("", 25.0), ("success", None)]}
    _issue(phone)
    assert progress.snapshot()["stage"] == progress.ACCEPT


# --- verification -------------------------------------------------------------------------------

def test_a_shared_presentation_completes_accept(phone):
    _verification_until_share(phone, [("pin", None)])
    phone.then["PIN"] = [("consent", None)]
    # The second PIN, after Share, leads to Go to wallet.
    original = phone.tap

    def tap(name):
        original(name)
        if name == "PIN" and phone.taps.count("PIN") == 2:
            phone.show(("success", None))
    phone.tap = tap
    _verify(phone)
    trail = progress.snapshot()
    assert phone.taps == ["PIN", "Yes, continue", "Share", "PIN", "Go to wallet"]
    assert trail["stage"] == progress.ACCEPT
    assert "entered the wallet PIN after Share" in _steps(trail)


def test_an_error_after_the_pin_is_rejected_at_open_without_doubled_quotes(phone):
    """waltid_verifier, 46 nights, and authbound_verifier until 2026-09-16."""
    phone.reason = WALTID_VERIFIER
    phone.show(("pin", None))
    phone.then = {"PIN": [("error", None)]}
    exc, trail = _raised(_verify, phone)
    assert exc.category == outcome.REJECTED
    assert wallet_said(exc) == "Presentation information could not be extracted"
    assert str(exc).count('"') == 2
    assert str(exc).endswith("[no_retry]")
    assert trail["stage"] == progress.OPEN


def test_unavailable_cards_is_no_match_at_present_and_stays_on_screen(phone):
    """procivis_verifier, all three attempts on each of 39 nights."""
    phone.show(("pin", None))
    phone.then = {"PIN": [("consent", None)], "Yes, continue": [("no_match", None)]}
    exc, trail = _raised(_verify, phone)
    assert exc.category == outcome.NO_MATCH
    assert wallet_said(exc) == "You don't have the required cards"
    assert str(exc).endswith("[no_retry]")
    assert trail["stage"] == progress.PRESENT
    assert phone.taps == ["PIN", "Yes, continue"], "nothing closes the evidence"


def test_an_error_after_share_is_rejected_after_the_match(phone):
    _verification_until_share(phone, [("error", None)])
    exc, trail = _raised(_verify, phone)
    assert exc.category == outcome.REJECTED
    assert "took the Share for 'pension_verification' and then failed" in str(exc)
    assert trail["stage"] == progress.MATCH


def test_go_to_wallet_after_share_with_the_error_heading_beside_it_is_not_success(phone):
    _verification_until_share(phone, [("", 1), ("error", None)])
    exc, trail = _raised(_verify, phone)
    assert exc.category == outcome.REJECTED
    assert trail["stage"] == progress.MATCH
    assert phone.waited("error")[-1] == failures._LAST_ERROR_LOOK


def test_a_hand_over_to_a_browser_passes_without_claiming_accept(phone):
    """igrant_playground verifiers, nightly 2026-10-26 and -27: Chrome in front after Share."""
    _verification_until_share(phone, [("", None)])
    original = phone.tap

    def tap(name):
        original(name)
        if name == "Share":
            phone.front = "com.android.chrome"
    phone.tap = tap
    _verify(phone)
    trail = progress.snapshot()
    assert trail["stage"] == progress.MATCH
    assert _steps(trail)[-1] == ("paradym handed over to com.android.chrome after Share (the "
                                 "verifier's redirect)")


def test_a_crash_dialog_wins_over_a_browser(phone):
    _verification_until_share(phone, [("", None)])
    phone.then["Share"] = [("", None)]
    original = phone.tap

    def tap(name):
        original(name)
        if name == "Share":
            phone.front = "com.android.chrome"
            phone.crash = SystemOverlay.APP_CRASH
    phone.tap = tap
    exc, _ = _raised(_verify, phone)
    assert exc.category == outcome.CRASHED


def test_another_app_after_share_is_never_a_pass(phone):
    _verification_until_share(phone, [("", None)])
    original = phone.tap

    def tap(name):
        original(name)
        if name == "Share":
            phone.front = "com.google.android.apps.nexuslauncher"
    phone.tap = tap
    exc, trail = _raised(_verify, phone)
    assert exc.category == outcome.PROCESSING
    assert "in the 30s after Share" in str(exc)
    assert trail["stage"] == progress.MATCH


def test_a_success_later_than_the_old_ten_seconds_still_passes(phone):
    _verification_until_share(phone, [("", 15.0), ("success", None)])
    _verify(phone)
    assert progress.snapshot()["stage"] == progress.ACCEPT


def test_an_error_later_than_the_old_ten_seconds_is_no_longer_a_pass(phone):
    """eudi_reference_pid_sdjwt_verifier, nightly 2026-10-27: the old flow returned after 10 s
    without Go to wallet and passed; its recording shows "Something went wrong" — Reason
    "Presentation could not be shared" — which teardown then tapped through."""
    phone.reason = ("Reason: Presentation could not be shared.\nDevelopment mode error: Error "
                    "while accepting authorization request.")
    _verification_until_share(phone, [("", 15.0), ("error", None)])
    exc, trail = _raised(_verify, phone)
    assert exc.category == outcome.REJECTED
    assert wallet_said(exc) == "Presentation could not be shared."
    assert trail["stage"] == progress.MATCH


def test_a_dead_session_after_share_is_ours(phone):
    _verification_until_share(phone, [("", None)])
    phone.dead_after = 15
    exc, _ = _raised(_verify, phone)
    assert exc.category == outcome.LOST_SESSION


def test_a_refused_verification_deeplink_is_unroutable(phone):
    phone.refuse = REFUSED
    exc, _ = _raised(_verify, phone)
    assert exc.category == outcome.UNROUTABLE


def test_no_request_on_the_home_screen_is_dismissed(phone):
    phone.show(("home", None))
    exc, trail = _raised(_verify, phone)
    assert exc.category == outcome.DISMISSED
    assert "not the verification request" in str(exc)
    assert trail["stage"] == progress.OPEN


def test_an_unavailable_cards_answer_after_the_wait_is_still_no_match(phone):
    phone.show(("", 30.0), ("no_match", None))
    exc, _ = _raised(_verify, phone)
    assert exc.category == outcome.NO_MATCH


# The published wording of the three helpers every paradym flow shares, spelled out in full.
def _ctx():
    return FlowContext(flow="credential_flow", wallet="paradym", what="credential_issuance",
                       app_package=PACKAGE)


def test_the_crash_message_is_exact(phone):
    phone.crash = SystemOverlay.APP_CRASH
    with pytest.raises(outcome.FlowFailure) as exc:
        failures.raise_if_crashed(phone, _ctx(), "after the deeplink")
    assert str(exc.value) == (
        "[crashed] [credential_flow] paradym crashed after the deeplink for "
        "'credential_issuance': Android showed its crash dialog")


def test_the_not_responding_message_is_exact(phone):
    phone.crash = SystemOverlay.ANR
    with pytest.raises(outcome.FlowFailure) as exc:
        failures.raise_if_crashed(phone, _ctx(), "after the deeplink")
    assert str(exc.value) == (
        "[crashed] [credential_flow] paradym stopped responding after the deeplink for "
        "'credential_issuance': Android showed its not-responding dialog")


def test_the_lost_session_message_is_exact(phone):
    assert str(failures.lost(_ctx(), "while entering the PIN", DEAD)) == (
        "[lost_session] [credential_flow] Appium/UiAutomator2 connection lost while entering the "
        "PIN for 'credential_issuance' (app may have crashed): instrumentation process is not "
        "running")


def test_the_shared_helpers_are_base_s_and_the_probes_are_not_here():
    for name in ("lost", "answer_permission", "first_of", "raise_if_crashed", "late_look"):
        assert getattr(failures, name) is getattr(flow_failures, name), name
    assert not hasattr(failures, "detect_crash_or_anr")
    assert not hasattr(failures, "handle_permission_if_present")
