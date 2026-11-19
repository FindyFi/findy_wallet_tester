"""Every way gataca's flows can end, mapped to the outcome it raises and how far it got.

Before, every gataca branch raised a bare RuntimeError ("No offer/error/home screen after 30s"),
its error probe matched one of three error screens (so three of the eight reds on 2026-11-18, gataca
2.6.5, read as "nothing appeared"), a bare home was a pass, the system auth sheet's failures were
three different untyped exceptions, and a count that saturated at what fits on screen failed a good
issuance.

Layers, none touching a device:

- the **page probes** run for real against structures copied from gataca's captured dumps
  (reports/2026-11-18_12-11-52/gataca/xml_dumps, and the 2.5.9 dumps for the no-match dialog),
  through a driver that evaluates their XPath with ElementTree;
- the **list counting** runs for real against `Wallet`, a virtual scrolling list that renders the
  page source the way UiAutomator does (cards clipped to the list, off-screen ones absent);
- the **flows** run for real against `Phone`, which answers each probe from a scripted sequence of
  screens and keeps a virtual clock, so a 30-second wait costs nothing.
"""
import re
import time
import xml.etree.ElementTree as ET
from types import SimpleNamespace
from xml.sax.saxutils import quoteattr

import pytest
from appium.webdriver.common.appiumby import AppiumBy
from selenium.common.exceptions import (
    NoSuchElementException,
    StaleElementReferenceException,
    TimeoutException,
    WebDriverException,
)

from base import cleanup as base_cleanup, deeplink, outcome, progress
from base.android import PinFieldMissing, SystemOverlay
from base.conftest_helpers import _QUOTED
from base.credential_count import CredentialCountUnavailable
from base.screens import UNKNOWN
from wallets.gataca.flows import _failures as failures
from wallets.gataca.flows import cleanup_flow, credential_flow as cf, init_flow, setup_flow
from wallets.gataca.flows import verification_flow as vf
from wallets.gataca.pages import credential_offer_page as offer_page
from wallets.gataca.pages import error_page, home_page, success_page
from wallets.gataca.pages.home_page import HomePage
from wallets.gataca.screens import SCREENS

PACKAGE = "com.gataca.identity"
URL = "openid-credential-offer://?credential_offer_uri=https%3A%2F%2Fissuer.example%2Foffer"
PAGE_ARGS = {"timeouts": {"default": 10, "credential_offer": 30}, "device_pin": "9876"}

REFUSED = ("Error attempting to start URI. Original error: Error: Starting: Intent "
           "{ act=android.intent.action.VIEW dat=eudi-openid4vp:///... pkg=" + PACKAGE + " }\n"
           "Error: Activity not started, unable to resolve Intent "
           "{ act=android.intent.action.VIEW dat=eudi-openid4vp:///... flg=0x10000000 "
           "pkg=" + PACKAGE + " }")
DEAD = WebDriverException("instrumentation process is not running")

# gataca's own words, from reports/2026-11-18_12-11-52/gataca/xml_dumps (2.6.5).
UNKNOWN_QR = ("The QR you just scanned is unknown and you cannot access its data. Please contact "
              "the service provider for help")
HOVI_HEADING = "Error requesting credentials"
AUTHBOUND_HEADING = ('[missing "en.PROOF_ALG_NOT_SUPPORTED: DID key algorithm \'EdDSA\' is not '
                     'accepted by the Issuer. Supported: [ES256]" translation]')
WALTID_HEADING = '[missing "en.Cannot read property \'processId\' of null" translation]'
PARADYM_HEADING = "QR Code is not working"
PARADYM_BODY = ("The QR you just scanned is not working. Try to refresh the page or alert the "
                "service provider that his qr configuration or credentials are broken.")
NO_MATCH_BODY = ("You are unable to answer the consent due to missing required credentials. "
                 "Please, make sure to acquire the needed credentials first.")


def wallet_said(exc) -> str:
    """What the app.log digest would publish as the wallet's words: the first quoted span."""
    m = _QUOTED.search(str(exc))
    return m.group(1) if m else ""


# --- captured screens ----------------------------------------------------------------------------

def _attrs(cls, text="", cd="", clickable=False, scrollable=False, bounds="[0,0][1080,2337]",
           package=PACKAGE):
    return (f'<{cls} text={quoteattr(text)} content-desc={quoteattr(cd)} '
            f'clickable="{str(clickable).lower()}" scrollable="{str(scrollable).lower()}" '
            f'bounds="{bounds}" package="{package}"')


def tv(text, bounds="[0,0][1080,100]", package=PACKAGE):
    return _attrs("android.widget.TextView", text=text, bounds=bounds, package=package) + "/>"


def button(cd, label=None, bounds="[53,2106][1028,2232]"):
    inner = tv(label if label is not None else cd)
    return _attrs("android.view.ViewGroup", cd=cd, clickable=True, bounds=bounds) + f">{inner}</android.view.ViewGroup>"


def screen(*body):
    return ('<?xml version="1.0" encoding="UTF-8"?><hierarchy>'
            f'<android.widget.FrameLayout package="{PACKAGE}">{"".join(body)}'
            '</android.widget.FrameLayout></hierarchy>')


def error_screen(heading, body=UNKNOWN_QR, extra="", before=""):
    return screen(before, tv(heading), tv(body), button("Go back"), extra)


TOASTS = ('<android.widget.Toast text="Issue reading the QR code" package="com.android.settings"/>'
          '<android.widget.Toast text="Gataca" package="com.android.settings"/>')

HOME_HEAD = (tv("My credentials") + button("Add", "Add", "[836,89][1038,194]")
             + _attrs("android.widget.Button", cd="JWK Identity", clickable=True,
                      bounds="[13,269][330,331]") + "/>")
OFFER = screen(tv("Sharing requirements"), tv("Sharing data with..."),
               button("Decline", bounds="[28,1383][350,1460]"),
               button("Share", bounds="[371,1383][692,1460]"))
# The dialog is laid OVER Sharing requirements: both are in the tree (2.5.9, 2026-08-27).
NO_MATCH = screen(
    tv("Sharing requirements"), button("Share", bounds="[371,1383][692,1460]"),
    _attrs("android.view.ViewGroup", cd="Unfortunately, giving consent is not possible, " + NO_MATCH_BODY)
    + ">" + tv(NO_MATCH_BODY) + tv("Unfortunately, giving consent is not possible")
    + button("Close") + button("Go back") + "</android.view.ViewGroup>")
REJECTED = screen(tv("Rejected"), tv("core-agent.hovi.id"), button("OK", bounds="[26,2185][1054,2311]"))
LOGIN = screen(tv("Login Successful"), button("OK"))
SHARED = screen(tv("Credentials Shared"), button("OK"))


class _Node:
    def __init__(self, screen_, node):
        self._screen, self._node = screen_, node
        self.id = f"node-{id(node)}"

    @property
    def text(self):
        return self._node.get("text", "")

    def get_attribute(self, name):
        return self._node.get(name, "")

    def click(self):
        self._screen.tapped.append(self._node.get("text") or self._node.get("content-desc", ""))


def _pred(expr):
    """An XPath predicate as a Python expression over `a(attribute)` — the subset gataca's
    locators use."""
    e = re.sub(r'string-length\((@[\w-]+)\)', r'len(\1)', expr)
    e = re.sub(r'starts-with\((@[\w-]+),\s*("[^"]*"|\'[^\']*\')\)', r'\1.startswith(\2)', e)
    e = re.sub(r'contains\((@[\w-]+),\s*("[^"]*"|\'[^\']*\')\)', r'(\2 in \1)', e)
    e = re.sub(r'@([\w-]+)', r'a("\1")', e)
    return re.sub(r'(?<![!<>=])=(?!=)', '==', e)


def _union(expr):
    parts, depth, quote, start = [], 0, "", 0
    for i, ch in enumerate(expr):
        if quote:
            quote = "" if ch == quote else quote
        elif ch in "\"'":
            quote = ch
        elif ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
        elif ch == "|" and depth == 0:
            parts.append(expr[start:i].strip())
            start = i + 1
    parts.append(expr[start:].strip())
    return parts


def xpath(root, expr):
    """Evaluate `//Tag[pred]//Tag[pred]` alternatives joined by `|` with ElementTree."""
    found, seen = [], set()
    for alt in _union(expr):
        steps = re.findall(r'(//|/)([\w.*-]+)(?:\[(.*?)\])?(?=//|/|$)', alt)
        assert steps and "".join(f"{s[0]}{s[1]}{'[' + s[2] + ']' if s[2] else ''}"
                                 for s in steps) == alt, f"locator not understood: {alt}"
        current = [None]
        for axis, tag, pred in steps:
            nxt = []
            for base in current:
                if base is None:
                    pool = list(root.iter())
                elif axis == "//":
                    pool = list(base.iter())[1:]
                else:
                    pool = list(base)
                for n in pool:
                    if tag not in ("*", n.tag):
                        continue
                    if pred and not eval(_pred(pred), {"len": len}, {"a": lambda k, n=n: n.get(k, "")}):
                        continue
                    nxt.append(n)
            current = nxt
        for n in current:
            if id(n) not in seen:
                seen.add(id(n))
                found.append(n)
    return found


class Screen:
    """One captured dump as a driver."""
    current_package = PACKAGE
    capabilities: dict = {}

    def __init__(self, xml):
        self.xml = xml
        self.root = ET.fromstring(xml.encode())
        self.tapped = []

    @property
    def page_source(self):
        return self.xml

    def find_elements(self, by, value):
        assert by == AppiumBy.XPATH, by
        return [_Node(self, n) for n in xpath(self.root, value)]

    def find_element(self, by, value):
        found = self.find_elements(by, value)
        if not found:
            raise NoSuchElementException(value)
        return found[0]


# --- the page probes, against the captured screens -----------------------------------------------

@pytest.mark.parametrize("heading, body", [
    (HOVI_HEADING, UNKNOWN_QR),
    (AUTHBOUND_HEADING, UNKNOWN_QR),
    (WALTID_HEADING, UNKNOWN_QR),
    (PARADYM_HEADING, PARADYM_BODY),
])
def test_the_error_probe_matches_all_three_shapes_of_gatacas_error_screen(heading, body):
    """The old probe matched only the first heading: the other shapes read as 'nothing appeared'."""
    assert error_page.present(Screen(error_screen(heading, body)), timeout=0.1)


def test_the_error_probe_matches_nothing_else():
    for other in (OFFER, NO_MATCH, REJECTED, LOGIN, SHARED, screen(HOME_HEAD), screen(tv("x"))):
        assert not error_page.present(Screen(other), timeout=0.1)
    no_button = screen(tv(AUTHBOUND_HEADING), tv(UNKNOWN_QR))
    assert not error_page.present(Screen(no_button), timeout=0.1), "the Go back button too"


def test_a_gataca_translation_key_is_read_as_its_reason_without_the_wrapper_or_double_quotes():
    said = error_page.message(Screen(error_screen(AUTHBOUND_HEADING)))
    assert said.startswith("PROOF_ALG_NOT_SUPPORTED: DID key algorithm 'EdDSA' is not accepted "
                           "by the Issuer. Supported: [ES256]. The QR you just scanned is unknown")
    assert '"' not in said and "[missing" not in said and "translation]" not in said
    waltid = error_page.message(Screen(error_screen(WALTID_HEADING)))
    assert waltid.startswith("Cannot read property 'processId' of null. The QR you just")


def test_the_generic_heading_adds_nothing_and_paradyms_own_sentence_is_kept():
    assert error_page.message(Screen(error_screen(HOVI_HEADING))) == UNKNOWN_QR
    said = error_page.message(Screen(error_screen(PARADYM_HEADING, PARADYM_BODY)))
    assert said == "QR Code is not working. " + PARADYM_BODY.rstrip(".")


def test_the_text_is_cut_to_a_readable_length():
    said = error_page.compose([PARADYM_HEADING, "The QR you just scanned " + "x" * 5000])
    assert len(said) == error_page.TEXT_LIMIT and said.endswith("…")


def test_a_toast_over_the_error_screen_is_not_part_of_what_gataca_said():
    """hovi, 2.6.5: toasts 'Issue reading the QR code' and 'Gataca' (package com.android.settings)
    sit over the screen, and their nodes go stale while they fade. Matched by `//*[@text]` they
    made the old reader raise, and every hovi message came out empty."""
    said = error_page.message(Screen(error_screen(HOVI_HEADING, extra=TOASTS)))
    assert said == UNKNOWN_QR
    assert "Issue reading" not in said and "Gataca" not in said


def test_a_toast_listed_before_the_error_texts_is_still_not_the_heading():
    said = error_page.message(Screen(error_screen(AUTHBOUND_HEADING, before=TOASTS)))
    assert said.startswith("PROOF_ALG_NOT_SUPPORTED") and "Issue reading" not in said


def test_double_quotes_in_what_gataca_said_cannot_pair_up_with_ours():
    said = error_page.compose(['Cannot find "processId" in the offer', UNKNOWN_QR])
    assert '"' not in said and said.startswith("Cannot find 'processId' in the offer")


def test_one_stale_node_costs_that_node_not_the_whole_reading():
    class Stale:
        def get_attribute(self, name):
            raise StaleElementReferenceException("gone")

    class Driver(Screen):
        def find_elements(self, by, value):
            return [Stale()] + super().find_elements(by, value)

    assert error_page.message(Driver(error_screen(AUTHBOUND_HEADING))).startswith(
        "PROOF_ALG_NOT_SUPPORTED")

    class Raising(Screen):
        def find_elements(self, by, value):
            class Odd:
                def get_attribute(self, name):
                    raise WebDriverException("odd")
            return [Odd()] + super().find_elements(by, value)

    assert error_page.message(Raising(error_screen(HOVI_HEADING))) == UNKNOWN_QR


def test_reading_the_error_never_raises():
    class Broken:
        def find_elements(self, *a):
            raise WebDriverException("gone")
    assert error_page.message(Broken()) == ""
    assert error_page.message(Screen(screen())) == ""


def test_the_no_match_dialog_is_found_even_though_the_request_and_share_are_still_under_it():
    assert offer_page.no_match(Screen(NO_MATCH), timeout=0.1)
    assert offer_page.on_screen(Screen(NO_MATCH), timeout=0.1), "why it must be probed first"
    assert not offer_page.no_match(Screen(OFFER), timeout=0.1)
    assert offer_page.no_match_text(Screen(NO_MATCH)) == NO_MATCH_BODY


def test_the_rejected_dialog_names_the_verifier_and_the_ok_taps_through_its_content_desc():
    dialog = Screen(REJECTED)
    assert success_page.on_rejected_screen(dialog, timeout=0.1)
    assert success_page.rejected_detail(dialog) == "core-agent.hovi.id"
    assert success_page.tap_rejected_ok(dialog) and dialog.tapped == ["OK"]
    other = Screen(OFFER)
    assert not success_page.tap_rejected_ok(other) and other.tapped == []


def test_verification_complete_is_probed_by_text_and_by_content_desc_and_issued_banner_apart():
    assert success_page.on_complete_screen(Screen(screen(tv("Verification Complete"))), 0.1)
    assert success_page.on_complete_screen(
        Screen(screen(_attrs("android.view.ViewGroup", cd="Verification Complete") + "/>")), 0.1)
    assert not success_page.on_complete_screen(Screen(SHARED), 0.1)
    assert success_page.on_issued_banner(
        Screen(screen(HOME_HEAD, tv("Credential Successfully Issued"))), 0.1)
    assert not success_page.on_terminal_screen(Screen(LOGIN), 0.1), "Login is not terminal"


def test_screens_declares_only_what_a_dump_shows():
    assert SCREENS.error(Screen(error_screen(AUTHBOUND_HEADING)), 0.1)
    assert SCREENS.error_text(Screen(error_screen(HOVI_HEADING))) == UNKNOWN_QR
    assert SCREENS.home(Screen(screen(HOME_HEAD)), 0.1) and not SCREENS.home(Screen(OFFER), 0.1)
    assert SCREENS.no_match(Screen(NO_MATCH), 0.1)
    assert SCREENS.processing(Screen(screen(tv("Sending your consent"))), 0.1)
    assert SCREENS.success is UNKNOWN


# --- counting every card, by scrolling a virtual list --------------------------------------------

# The system fingerprint sheet over gataca, as Appium reported it (emulator-5554, 2026-11-19).
FINGERPRINT_SHEET = (
    '<?xml version="1.0" encoding="UTF-8"?><hierarchy>'
    '<android.widget.LinearLayout package="com.android.systemui" bounds="[0,0][1080,2337]">'
    '<android.widget.TextView text="Perform biometric verification" '
    'package="com.android.systemui" bounds="[100,900][980,980]"/>'
    '<android.widget.TextView text="Touch the fingerprint sensor" '
    'package="com.android.systemui" bounds="[100,990][980,1050]"/>'
    '<android.widget.ImageView resource-id="com.android.systemui:id/biometric_icon" '
    'package="com.android.systemui" bounds="[440,1100][640,1300]"/>'
    '<android.widget.Button text="Cancel" resource-id="com.android.systemui:id/button_negative" '
    'package="com.android.systemui" bounds="[400,1500][680,1600]"/>'
    '</android.widget.LinearLayout></hierarchy>')


class Wallet:
    """gataca's home with a scrolling credentials list, as UiAutomator would dump it: the list
    is [0,517][1080,2153], cards are clipped to it, and cards entirely outside it are absent.

    `cards` is [(content-desc, height)], laid out with a 52px gap from 26px under the top. A
    scroll gesture moves the list by `percent` of its height times `factor` — a factor other than
    1.0 is a gesture that did not move as far as it was asked to.
    """
    TOP, BOTTOM, GAP = 517, 2153, 52
    capabilities: dict = {}
    current_package = PACKAGE

    def __init__(self, cards, factor=1.0, scrollable=True, on_home=True, chips=True, extra=0,
                 toggles=True, loading=0, empty_state=False, refresh_sheet=True, banner=0,
                 moving=False):
        # banner: the first N reads show the green "Credential Successfully Issued" banner with
        # the whole page pushed down 130 px. moving: every read finds the page somewhere else.
        self.banner, self.moving = banner, moving
        # loading: the first N page-source reads show "No credentials yet" and no list.
        # empty_state: with no list, show "No credentials yet" (a wallet that holds nothing).
        # refresh_sheet: an "up" gesture that starts at the top raises the system fingerprint
        # sheet (measured on 2.6.5), which stays until it is answered.
        self.loading, self.empty_state, self.refresh_sheet = loading, empty_state, refresh_sheet
        self.sheet, self.up_starts, self.sheets_raised, self.reads = False, [], 0, 0
        self.after_sheet = 0
        self.cards, self.factor, self.scrollable, self.on_home = cards, factor, scrollable, on_home
        self.chips, self.toggles = chips, toggles
        self.offset, self.gestures, self.clicked, self.view_taps = 0.0, [], [], []
        pos, self.positions = 26, []
        for _, height in cards:
            self.positions.append(pos)
            pos += height + self.GAP
        self.content = pos - self.GAP + 26 + extra
        self.max_offset = max(0.0, self.content - (self.BOTTOM - self.TOP))

    @property
    def page_source(self):
        self.reads += 1
        if self.sheet:
            return FINGERPRINT_SHEET
        if self.after_sheet > 0:
            self.after_sheet -= 1
            return screen(tv("My credentials"), button("All"), button("Favorites"))
        if self.reads <= self.loading:
            return screen(tv("My credentials"), button("All"), button("Favorites"),
                          tv("Sorry!", bounds="[469,1399][612,1462]"),
                          tv("No credentials yet", bounds="[374,1483][707,1536]"))
        dy = 130 if self.reads <= self.banner else (37 * self.reads if self.moving else 0)
        T, B = self.TOP + dy, self.BOTTOM + dy
        cards = []
        for (desc, height), pos in zip(self.cards, self.positions):
            y0, y1 = T + pos - self.offset, T + pos - self.offset + height
            if y1 <= T or y0 >= B:
                continue
            top, bottom = round(max(y0, T)), round(min(y1, B))
            cards.append(_attrs("android.view.ViewGroup", cd=desc, clickable=True,
                                bounds=f"[42,{top}][1038,{bottom}]") + ">"
                         + tv(desc.split(",")[0], bounds=f"[97,{top}][654,{top + 40}]")
                         + "</android.view.ViewGroup>")
        listing = ""
        if self.scrollable:
            listing = (_attrs("android.widget.ScrollView", scrollable=True,
                              bounds=f"[0,{T}][1080,{B}]") + ">"
                       + "".join(cards) + "</android.widget.ScrollView>")
        chips = "" if not self.chips else (_attrs("android.widget.HorizontalScrollView", scrollable=True,
                        bounds="[391,418][1038,493]") + ">"
                 + button("All", bounds="[391,418][483,493]")
                 + button("Favorites", bounds="[515,418][713,493]") + "</android.widget.HorizontalScrollView>")
        # The two unlabelled view buttons (stacked deck, then list) and the selection pill.
        toggles = "" if not self.toggles else (
            _attrs("android.view.ViewGroup", bounds="[42,407][305,501]") + ">"
            + _attrs("android.view.ViewGroup", bounds="[177,413][298,497]") + "/>"
            + _attrs("android.view.ViewGroup", clickable=True, bounds="[47,412][168,496]") + "/>"
            + _attrs("android.view.ViewGroup", clickable=True, bounds="[178,412][299,496]") + "/>"
            + "</android.view.ViewGroup>")
        empty = (tv("Sorry!") + tv("No credentials yet")) if (
            self.empty_state and not listing) else ""
        if self.reads <= self.banner:
            empty += _attrs("android.view.ViewGroup", cd="Credential Successfully Issued",
                            bounds="[0,0][1080,130]") + ">" + tv("Credential Successfully Issued") \
                + "</android.view.ViewGroup>"
        return screen((tv("My credentials") if self.on_home else tv("Elsewhere")),
                      button("Add", bounds="[836,89][1038,194]"), toggles, chips, listing, empty,
                      button("Scan", bounds="[805,1982][1022,2087]"))

    def _dom(self):
        return Screen(self.page_source)

    def find_elements(self, by, value):
        dom = self._dom()
        nodes = dom.find_elements(by, value)
        for n in nodes:
            n._screen = self
        return nodes

    def find_element(self, by, value):
        found = self.find_elements(by, value)
        if not found:
            raise NoSuchElementException(value)
        return found[0]

    @property
    def tapped(self):
        return self.clicked

    def execute_script(self, script, args):
        if script == "mobile: clickGesture":
            self.view_taps.append((args["x"], args["y"], len(self.gestures)))
            return None
        assert script == "mobile: scrollGesture", script
        self.gestures.append(args)
        delta_ = args["percent"] * (self.BOTTOM - self.TOP) * self.factor
        if args["direction"] == "up":
            self.up_starts.append(self.offset)
            # A pull that asks for more than is left (past the top) refreshes the list: the
            # fingerprint sheet, and afterwards the list reloads for a few reads.
            if self.refresh_sheet and delta_ > self.offset + 1.0:
                self.sheet = True
                self.sheets_raised += 1
                self.offset = 0.0
                return False
        delta = args["percent"] * (self.BOTTOM - self.TOP) * self.factor
        if args["direction"] == "down":
            self.offset = min(self.max_offset, self.offset + delta)
            return self.offset < self.max_offset
        self.offset = max(0.0, self.offset - delta)
        return self.offset > 0

    def answer_sheet(self):
        """What authenticate_device does for the real thing: the sheet goes away."""
        was, self.sheet = self.sheet, False
        if was:
            self.after_sheet = 3
        return was


def _home(driver, default=0.1):
    return HomePage(driver, timeouts={"default": default}, device_pin="9876")


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    """A virtual clock: sleeping advances it, so waits cost nothing."""
    now = [1000.0]
    monkeypatch.setattr(time, "monotonic", lambda: now[0])
    monkeypatch.setattr(time, "sleep", lambda s: now.__setitem__(0, now[0] + s))
    return now


@pytest.fixture(autouse=True)
def answers(monkeypatch):
    """authenticate_device in the home page, answering the fake wallet's sheet."""
    calls = []

    def answer(driver, pin, *a, **k):
        calls.append(pin)
        return driver.answer_sheet()
    monkeypatch.setattr(home_page, "authenticate_device", answer)
    return calls


def _oma(n):
    return [("OmaKela Pensiondemo", 562)] * n


def _kela(n):
    return [("Kela", 86)] * n


def test_the_count_picks_the_list_view_before_it_scrolls():
    # 2026-11-19: the view had been left on gataca's stacked "deck", where the cards overlap and
    # the scroll count failed on every issuance. The count now taps the list-view button first.
    wallet = Wallet(_oma(5))
    assert _home(wallet).count_credentials() == 5
    assert wallet.view_taps == [(238, 454, 0)], "the list view (second button), before any scroll"


def test_the_count_goes_on_when_there_is_no_view_toggle():
    wallet = Wallet(_oma(2), toggles=False)
    assert _home(wallet).count_credentials() == 2
    assert wallet.view_taps == []


def test_the_view_toggle_is_found_in_a_real_dump():
    # From a 2.6.5 dump on emulator-5554 (2026-11-19): pill, stacked button, list button. The
    # filter chips (two clickable ViewGroups that DO carry content-desc) must not match.
    source = screen(
        _attrs("android.view.ViewGroup", bounds="[42,407][305,501]") + ">"
        + _attrs("android.view.ViewGroup", bounds="[177,413][298,497]") + "/>"
        + _attrs("android.view.ViewGroup", clickable=True, bounds="[47,412][168,496]") + "/>"
        + _attrs("android.view.ViewGroup", clickable=True, bounds="[178,412][299,496]") + "/>"
        + "</android.view.ViewGroup>",
        _attrs("android.view.ViewGroup", bounds="[391,418][1038,493]") + ">"
        + _attrs("android.view.ViewGroup", cd="All", clickable=True, bounds="[391,418][483,493]") + "/>"
        + _attrs("android.view.ViewGroup", cd="Favorites", clickable=True,
                 bounds="[515,418][713,493]") + "/>" + "</android.view.ViewGroup>")
    assert home_page.list_view_toggle(source) == (238, 454)
    assert home_page.list_view_toggle(screen(tv("My credentials"))) is None


def test_the_saturated_list_is_counted_in_full():
    """The 2026-11-18 procivis run: three identical cards on screen, the third cut at the list's
    bottom edge, 3 -> 3 read as 'not stored'. Counting the nodes gives 3 for any longer list."""
    wallet = Wallet(_oma(5))
    assert len(wallet.find_elements(*home_page._credential_card)) == 3
    assert _home(wallet).count_credentials() == 5


@pytest.mark.parametrize("cards", [
    _oma(3), _oma(4), _oma(9), _kela(12),
    [("Self-attested, Device Id, 356c94c51da403ef", 562), ("Gataca Attest, Name, x", 562)]
    + _oma(3),
    [("A", 562), ("B", 86), ("B", 86), ("C", 300), ("A", 562), ("B", 86), ("D", 420)],
    [("Kela", 86)] * 2 + [("Pension", 562)] + [("Kela", 86)] * 6,
])
def test_every_card_is_counted_once_however_identical_they_are(cards):
    assert _home(Wallet(cards)).count_credentials() == len(cards)


def test_a_list_that_fits_is_counted_without_wandering_off():
    wallet = Wallet(_oma(1))
    assert _home(wallet).count_credentials() == 1
    assert wallet.offset == 0


def test_the_list_is_left_scrolled_to_its_top_for_the_next_reader():
    wallet = Wallet(_kela(12))
    _home(wallet).count_credentials()
    assert wallet.offset <= 33, "back at the top, but for what is under the smallest gesture"


def test_a_list_left_scrolled_is_brought_to_its_top_in_small_steps_and_counted():
    # Not one of ours (we always return to the top): found at an offset where no card is 26px
    # under the list's edge. Small steps, and the count starts from the top.
    wallet = Wallet(_oma(6))
    wallet.offset = 700
    assert _home(wallet, default=10).count_credentials() == 6


@pytest.mark.parametrize("factor", [0.75, 0.8, 0.9, 1.1, 1.3])
def test_a_gesture_a_fifth_or_more_off_still_counts_identical_cards_right(factor):
    """Real gestures lose ~20% to margins and touch slop."""
    assert _home(Wallet(_kela(12), factor=factor)).count_credentials() == 12
    assert _home(Wallet(_kela(40), factor=factor)).count_credentials() == 40


def test_a_gesture_far_off_is_unavailable_for_identical_cards_rather_than_a_guess():
    with pytest.raises(CredentialCountUnavailable, match="no telling which"):
        _home(Wallet(_kela(40), factor=2.0)).count_credentials()


def test_distinct_titles_need_no_accurate_gesture_to_be_counted_right():
    cards = [(f"Credential {i}", 300) for i in range(8)]
    assert _home(Wallet(cards, factor=2.0)).count_credentials() == 8


def test_an_empty_list_and_a_wrong_screen_are_not_the_same_answer():
    assert _home(Wallet([], scrollable=False, chips=False, empty_state=True)
                 ).count_credentials() == 0
    with pytest.raises(CredentialCountUnavailable, match="not showing"):
        _home(Wallet(_oma(3), on_home=False)).count_credentials()


def test_a_missing_list_with_no_empty_text_is_not_an_empty_wallet(clock):
    with pytest.raises(CredentialCountUnavailable, match="neither a credentials list nor"):
        _home(Wallet(_oma(3), scrollable=False)).count_credentials()


@pytest.mark.parametrize("after", [1, 2])
def test_a_list_that_vanishes_mid_scroll_is_unavailable_not_a_short_count(monkeypatch, after):
    wallet = Wallet(_oma(6))
    home = _home(wallet)
    real = home._page
    calls = []

    def page():
        calls.append(1)
        return None if len(calls) > after else real()
    monkeypatch.setattr(home, "_page", page)
    with pytest.raises(CredentialCountUnavailable, match="vanished"):
        home.count_credentials()


def test_a_last_scroll_of_a_few_pixels_is_the_same_page_not_a_move_to_line_up():
    """Content 3 px taller than the list: the final gesture moves it 3 px, under the tolerance."""
    wallet = Wallet([("A", 700), ("B", 835)], extra=0)
    assert wallet.max_offset == 3
    assert _home(wallet).count_credentials() == 2


def test_the_scroll_steps_stay_below_the_spacing_of_identical_cards():
    wallet = Wallet(_kela(12))
    _home(wallet).count_credentials()
    downs = [g for g in wallet.gestures if g["direction"] == "down"]
    assert downs and all(g["percent"] * 1636 < 139 for g in downs)


def test_cleanup_looks_for_a_deletable_card_below_the_ones_on_screen():
    wallet = Wallet([("Self-attested, Device Id, 1", 562), ("Self-attested, Name, 2", 562),
                     ("Self-attested, Other, 3", 562), ("Gataca Attest, Name, 4", 562)])
    assert _home(wallet).open_credential() is True
    assert wallet.clicked == ["Gataca Attest, Name, 4"]
    only_attested = Wallet([("Self-attested, Device Id, 1", 562)] * 4)
    assert _home(only_attested).open_credential() is False and only_attested.clicked == []


def test_cleanup_goes_on_pruning_when_the_scroll_count_is_unavailable(monkeypatch):
    """base/cleanup.py stops on an unreadable count, so gataca hands it a count that falls back to
    the cards on screen; the wallet would otherwise be left dirty for the next run."""
    class FakeHome:
        def __init__(self, driver, **kw):
            self.cards = 3

        def wait_until_loaded(self):
            pass

        def count_credentials(self):
            raise CredentialCountUnavailable("could not line the readings up")

        def visible_cards(self):
            return self.cards

        def open_credential(self):
            return self.cards > 0

    fake = FakeHome(None)

    class FakeDetail:
        def __init__(self, driver, **kw):
            pass

        def wait_until_loaded(self):
            pass

        def delete(self):
            fake.cards -= 1

    monkeypatch.setattr(cleanup_flow, "HomePage", lambda d, **kw: fake)
    monkeypatch.setattr(cleanup_flow, "CredentialDetailPage", FakeDetail)
    monkeypatch.setattr(cleanup_flow, "_INTERSTITIALS", ())
    assert cleanup_flow.prune_credentials(object(), max_count=0) == 3
    assert fake.cards == 0


# --- gataca, scripted, on a virtual clock --------------------------------------------------------

# A screen that also satisfies other probes: the no-match dialog lies over the request, and the
# completion modals lie over home, whose heading stays in the tree under them.
_ALSO = {"no_match": {"offer"}, "complete": {"home"}, "shared": {"home"},
         "home_banner": {"home", "banner"}, "processing_home": {"processing", "home"},
         "offer_email": {"offer", "email"},
         "no_match_email": {"no_match", "offer", "email"}}


class Phone:
    """What gataca shows, as a driver whose probes are answered from a script.

    `show(...)` sets what is on screen now, and `then` maps a tap to what follows it (a dict keyed
    by the screen it was tapped on, where one tap name serves several screens). Either is a list
    of (screen, length) segments, the last of which stays: an int length is a number of looks by
    any probe, a float one is seconds on the clock. A look at a screen that is not there advances
    the clock by the probe's timeout, as `wait_present` would; one that is there costs nothing.
    """
    capabilities: dict = {}

    def __init__(self):
        self.clock = 1000.0
        self.front = PACKAGE
        self.app_state = 3
        self.refuse = None
        self.dead_after = None
        self.crash = None
        self.error_text = "gataca said this"
        self.host = "core-agent.hovi.id"
        self.auth_exc = None
        self.then = {}
        self.asked, self.taps, self.auth_calls = [], [], []
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

    def authenticate(self, driver, pin, detect_timeout=2, dismiss_timeout=10):
        self.auth_calls.append((pin, detect_timeout, dismiss_timeout))
        self.asked.append(("auth", detect_timeout))
        assert len(self.asked) < 5000, "the flow is looping instead of reaching a verdict"
        if self.auth_exc:
            raise self.auth_exc
        on = self.screen()
        if on.startswith("auth"):
            self.tap(on)
            return True
        self.clock += detect_timeout
        return False

    def tap(self, name):
        self.taps.append(name)
        nxt = self.then.get(name)
        if isinstance(nxt, dict):
            nxt = nxt.get(self.screen())
        if nxt is not None:
            self.show(*nxt)

    def execute_script(self, script, args):
        if self.refuse:
            raise WebDriverException(self.refuse)


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
    monkeypatch.setattr(time, "sleep", lambda s: None)
    monkeypatch.setattr(error_page, "present", p.probe("error"))
    monkeypatch.setattr(error_page, "message", lambda d: p.error_text)
    monkeypatch.setattr(success_page, "on_terminal_screen", p.probe("shared"))
    monkeypatch.setattr(success_page, "on_login_screen", p.probe("login"))
    monkeypatch.setattr(success_page, "on_rejected_screen", p.probe("rejected"))
    monkeypatch.setattr(success_page, "on_complete_screen", p.probe("complete"))
    monkeypatch.setattr(success_page, "on_issued_banner", p.probe("banner"))
    monkeypatch.setattr(success_page, "rejected_detail", lambda d: p.host)
    monkeypatch.setattr(error_page, "go_back_now", lambda d: p.tap("Go back") or True)
    monkeypatch.setattr(success_page, "tap_rejected_ok", lambda d: p.tap("OK") or True)
    monkeypatch.setattr(offer_page, "on_screen", p.probe("offer"))
    monkeypatch.setattr(offer_page, "no_match", p.probe("no_match"))
    monkeypatch.setattr(offer_page, "processing", p.probe("processing"))
    monkeypatch.setattr(offer_page, "email_unavailable", p.probe("email"))
    monkeypatch.setattr(offer_page, "no_match_text", lambda d: NO_MATCH_BODY)
    for module in (cf, vf, failures):
        monkeypatch.setattr(module, "home_on_screen", p.probe("home"))
    monkeypatch.setattr(failures, "handle_permission_if_present", lambda d: False)
    monkeypatch.setattr(failures, "detect_crash_or_anr", lambda d, *a, **k: p.crash)
    monkeypatch.setattr(failures, "authenticate_device", p.authenticate)
    monkeypatch.setattr(failures, "wait_present",
                        lambda d, locator, timeout=2: p.screen().startswith("auth"))
    for module in (cf, vf):
        monkeypatch.setattr(module, "SuccessPage", _page(p, confirm="OK"))
        monkeypatch.setattr(module, "CredentialOfferPage", _page(p, accept="Share"))
    monkeypatch.setattr(deeplink, "unroutable_reason", lambda *a, **k: "")
    monkeypatch.setattr(deeplink, "registered_schemes", lambda *a, **k: [])
    monkeypatch.setattr(outcome, "detect_crash_or_anr", lambda *a, **k: None)
    yield p
    progress.reset()


def _issue(phone):
    cf.run(phone, provider=SimpleNamespace(get=lambda n: URL), credential_name="credential_issuance",
           app_package=PACKAGE, pin="123456", **PAGE_ARGS)


def _verify(phone):
    vf.run(phone, provider=SimpleNamespace(get=lambda n: URL), credential_name="pension_verification",
           app_package=PACKAGE, pin="123456", **PAGE_ARGS)


def _raised(fn, *args, **kw):
    with pytest.raises(outcome.FlowFailure) as exc:
        fn(*args, **kw)
    return exc.value, progress.snapshot()


def _steps(trail):
    return [s.split(": ", 1)[1] for s in trail["steps"]]


# --- the deeplink --------------------------------------------------------------------------------

@pytest.mark.parametrize("flow", ["issue", "verify"])
def test_a_refused_deeplink_is_unroutable_not_a_raw_driver_exception(phone, flow):
    """authbound_verifier (eudi-openid4vp://) and waltid_verifier (openid4vp://authorize) were
    both published as 'WebDriverException: Message: Error attempting to start URI'."""
    phone.refuse = REFUSED
    exc, trail = _raised(_issue if flow == "issue" else _verify, phone)
    assert exc.category == outcome.UNROUTABLE
    assert str(exc).endswith("[no_retry]")
    assert trail["stage"] == ""


def test_a_deeplink_failure_android_did_not_explain_is_ours(phone):
    phone.refuse = "adb: device offline"
    exc, _ = _raised(_verify, phone)
    assert exc.category == outcome.LOST_SESSION


# --- issuance: gataca's error screens ------------------------------------------------------------

@pytest.mark.parametrize("texts, said, first", [
    ([HOVI_HEADING, UNKNOWN_QR], UNKNOWN_QR, "The QR you just scanned is unknown"),
    ([AUTHBOUND_HEADING, UNKNOWN_QR], None, "PROOF_ALG_NOT_SUPPORTED: DID key algorithm 'EdDSA'"),
    ([WALTID_HEADING, UNKNOWN_QR], None, "Cannot read property 'processId' of null"),
    ([PARADYM_HEADING, PARADYM_BODY], None, "QR Code is not working"),
])
def test_each_error_shape_is_rejected_at_open_quoting_what_gataca_said(phone, texts, said, first):
    """The three reds on 2026-11-18 that read 'No offer/error/home screen after 30s'."""
    phone.error_text = error_page.compose(texts)
    phone.show(("error", None))
    exc, trail = _raised(_issue, phone)
    assert exc.category == outcome.REJECTED
    assert str(exc).startswith(
        "[rejected] [credential_flow] gataca could not process the credential offer for "
        f"'credential_issuance': \"{first}")
    assert wallet_said(exc).startswith(first)
    assert str(exc).endswith("[no_retry]") and "\n" not in str(exc)
    assert trail["stage"] == progress.OPEN
    assert phone.clock - 1000 < 5, "the error ends the wait; it is not found at the end"


def test_the_error_screen_is_left_up_for_the_failure_dump(phone):
    phone.show(("error", None))
    _raised(_issue, phone)
    assert phone.taps == [], "no Go back tap before the raise: init_flow recovers in teardown"


def test_an_error_whose_text_could_not_be_read_says_so_and_is_still_worth_a_rerun(phone):
    phone.error_text = ""
    phone.show(("error", None))
    exc, _ = _raised(_issue, phone)
    assert exc.category == outcome.REJECTED
    assert "(its text could not be read)" in str(exc) and "[no_retry]" not in str(exc)


def test_an_error_after_share_is_rejected_with_the_trail_at_present(phone):
    phone.show(("offer", None))
    phone.then = {"Share": [("auth", None)], "auth": [("error", None)]}
    exc, trail = _raised(_issue, phone)
    assert exc.category == outcome.REJECTED
    assert "took the Share for 'credential_issuance' and then failed" in str(exc)
    assert trail["stage"] == progress.PRESENT


# --- issuance: what a bare home means ------------------------------------------------------------

def test_a_home_screen_that_never_showed_an_offer_is_dismissed_not_a_pass(phone):
    """The old flow logged 'App returned to home without showing offer screen' and passed."""
    phone.show(("home", None))
    exc, trail = _raised(_issue, phone)
    assert exc.category == outcome.DISMISSED
    assert "showed its home screen, not the credential offer" in str(exc)
    assert trail["stage"] == progress.OPEN


def test_gataca_already_on_home_when_the_link_is_fired_is_not_dismissed_before_it_reacts(phone):
    """It is on home at the moment of the deeplink; a probe of home on the first pass would end
    every wait there."""
    phone.show(("home", 6.0), ("offer", None))
    phone.then = {"Share": [("auth", None)], "auth": [("home", None)]}
    _issue(phone)
    assert phone.taps == ["Share", "auth"]


def test_nothing_recognised_in_front_is_processing_and_never_a_dismissal(phone):
    phone.show(("unknown", None))
    exc, _ = _raised(_issue, phone)
    assert exc.category == outcome.PROCESSING
    assert "showed no" in str(exc) and "that this framework recognises" in str(exc)


def test_gataca_still_sending_its_consent_is_processing(phone):
    phone.show(("processing", None))
    exc, trail = _raised(_issue, phone)
    assert exc.category == outcome.PROCESSING
    assert "still on 'Sending your consent'" in str(exc)
    assert trail["stage"] == progress.OPEN


def test_a_crash_dialog_is_crashed_not_nothing_appeared(phone):
    phone.crash = SystemOverlay.APP_CRASH
    phone.show(("unknown", None))
    exc, _ = _raised(_issue, phone)
    assert exc.category == outcome.CRASHED


def test_a_dead_session_is_lost_session_never_a_verdict_about_gataca(phone):
    phone.show(("unknown", None))
    phone.dead_after = 4
    exc, _ = _raised(_issue, phone)
    assert exc.category == outcome.LOST_SESSION


# --- issuance: the happy paths and their trail ---------------------------------------------------

def test_an_offer_path_issuance_answers_every_sheet_and_leaves_keep_to_the_count(phone):
    """The pension flow: offer, Share, sheet, Login Successful, OK, sheet, home."""
    phone.show(("offer", None))
    phone.then = {"Share": [("auth", None)], "auth": [("login", None)],
                  "OK": {"login": [("auth2", None)]}, "auth2": [("home", None)]}
    _issue(phone)
    trail = progress.snapshot()
    assert phone.taps == ["Share", "auth", "OK", "auth2"]
    assert trail["stage"] == progress.PRESENT
    assert "tapped Share" in _steps(trail)
    assert "confirmed Login Successful (the OIDC step, not the credential)" in _steps(trail)
    assert any("accept not claimed" in s for s in _steps(trail))


def test_a_direct_issuance_answers_the_sheet_after_credentials_shared_before_calling_it_done(phone):
    """procivis, 2026-11-18: after OK on Credentials Shared a system sheet came up and nobody
    answered it, so two attempts failed on 'home screen did not load'."""
    phone.show(("auth", None))
    phone.then = {"auth": [("shared", None)], "OK": {"shared": [("auth2", None)]},
                  "auth2": [("home_banner", None)]}
    _issue(phone)
    trail = progress.snapshot()
    assert phone.taps == ["auth", "OK", "auth2"]
    assert trail["stage"] == progress.ACCEPT
    assert "gataca's home showed 'Credential Successfully Issued'" in _steps(trail)


def test_credentials_shared_with_no_home_after_it_is_processing_with_accept_kept(phone):
    phone.show(("shared", None))
    phone.then = {"OK": [("unknown", None)]}
    exc, trail = _raised(_issue, phone)
    assert exc.category == outcome.PROCESSING
    assert "whether it stored the credential is unknown" in str(exc)
    assert trail["stage"] == progress.ACCEPT


def test_a_login_successful_that_will_not_go_away_is_a_prompt_loop(phone):
    phone.show(("login", None))
    exc, _ = _raised(_issue, phone)
    assert exc.category == outcome.PROMPT_LOOP and "Login Successful" in str(exc)


def test_the_missing_email_credential_is_ours_to_provide_and_the_screen_stays_up(phone):
    phone.show(("offer_email", None))
    exc, trail = _raised(_issue, phone)
    assert exc.category == outcome.SETUP
    assert "Email credential not available for the active DID" in str(exc)
    assert phone.taps == []
    assert trail["stage"] == progress.OPEN


# --- the system auth sheet -----------------------------------------------------------------------

def test_a_fingerprint_only_phone_sheet_is_setup_naming_the_missing_pin_field(phone):
    phone.show(("auth", None))
    phone.auth_exc = PinFieldMissing("[android] PIN entry field did not appear after switching to PIN")
    exc, _ = _raised(_issue, phone)
    assert exc.category == outcome.SETUP
    assert "offers only a fingerprint" in str(exc) and "PIN fallback missing" in str(exc)


def test_a_pin_the_phone_accepted_but_whose_sheet_stayed_is_setup_not_lost_session(phone):
    """TimeoutException is a WebDriverException: caught after it, it was called a dead session."""
    phone.show(("auth", None))
    phone.auth_exc = TimeoutException("[android] PIN was accepted but the system auth UI did not "
                                      "close within 10s")
    exc, _ = _raised(_issue, phone)
    assert exc.category == outcome.SETUP
    assert "did not close after the device PIN was accepted" in str(exc)


def test_a_fingerprint_that_never_registers_is_setup_after_three_tries_not_a_30_second_wait(phone):
    phone.show(("auth", None))     # no `then`: the sheet stays up after every fingerprint
    exc, _ = _raised(_issue, phone)
    assert exc.category == outcome.SETUP
    assert "stayed up after 3 simulated fingerprints" in str(exc)
    assert "no enrolled fingerprint" in str(exc)
    assert phone.clock - 1000 < 30
    assert len(phone.auth_calls) == 3
    assert all(call[2] <= 5 for call in phone.auth_calls), "a short dismiss wait per try"


def test_a_sheet_that_keeps_coming_back_is_a_prompt_loop(phone, monkeypatch):
    """Every answer clears the sheet (the re-check sees it gone) and another one follows."""
    monkeypatch.setattr(failures, "wait_present", lambda *a, **k: False)
    phone.show(("auth", None))
    phone.then = {"auth": [("auth", None)]}
    exc, _ = _raised(_issue, phone)
    assert exc.category == outcome.PROMPT_LOOP and "7 times" in str(exc)


# --- verification --------------------------------------------------------------------------------

def _request_until_share(phone, after_share):
    phone.show(("offer", None))
    phone.then = {"Share": after_share}


def test_the_no_match_dialog_is_checked_before_the_request_it_lies_over(phone):
    """Both are in the tree (2.5.9), and Share is one of them: probed second, the dialog would
    be tapped past and the Share pressed on a request gataca said it could not answer."""
    phone.show(("no_match", None))
    exc, trail = _raised(_verify, phone)
    assert exc.category == outcome.NO_MATCH
    assert str(exc).endswith("[no_retry]")
    assert wallet_said(exc) == NO_MATCH_BODY
    assert trail["stage"] == progress.PRESENT
    assert phone.taps == [], "left up for the dump"


def test_the_no_match_dialog_rendering_a_moment_after_the_request_still_stops_the_share(phone):
    phone.show(("offer", 1), ("no_match", None))
    exc, _ = _raised(_verify, phone)
    assert exc.category == outcome.NO_MATCH and phone.taps == []


def test_the_verifiers_rejected_dialog_is_rejected_naming_the_verifier(phone):
    """hovi_verifier, 2.6.5: 'Rejected', 'core-agent.hovi.id', OK — three attempts, no body."""
    _request_until_share(phone, [("auth", None)])
    phone.then["auth"] = [("rejected", None)]
    exc, trail = _raised(_verify, phone)
    assert exc.category == outcome.REJECTED
    assert str(exc) == ("[rejected] [verification_flow] Credential verification for "
                        "'pension_verification' was rejected by core-agent.hovi.id: \"Rejected\" "
                        "[no_retry]")
    assert "wrong DID method" not in str(exc)
    assert "OK" not in phone.taps, "the dialog stays up for the dump"
    assert trail["stage"] == progress.MATCH


def test_verification_complete_over_home_is_the_accept_and_is_probed_before_home(phone):
    """'My credentials' is still in the tree under the modal, so home would win and the pass
    would carry no accept."""
    _request_until_share(phone, [("auth", None)])
    phone.then["auth"] = [("complete", None)]
    _verify(phone)
    trail = progress.snapshot()
    assert trail["stage"] == progress.ACCEPT
    assert phone.taps == ["Share", "auth", "OK"]
    assert "gataca showed Verification Complete" in " ".join(_steps(trail))


def test_a_bare_home_after_share_passes_but_claims_no_accept(phone):
    _request_until_share(phone, [("auth", None)])
    phone.then["auth"] = [("home", None)]
    _verify(phone)
    trail = progress.snapshot()
    assert trail["stage"] == progress.MATCH
    assert any("accept not claimed" in s for s in _steps(trail))


def test_a_bare_home_with_no_sheet_passes_once_nothing_else_is_showing(phone):
    _request_until_share(phone, [("home", None)])
    _verify(phone)
    assert progress.snapshot()["stage"] == progress.MATCH


def test_nothing_after_share_is_processing_with_whether_it_arrived_unknown(phone):
    _request_until_share(phone, [("unknown", None)])
    exc, trail = _raised(_verify, phone)
    assert exc.category == outcome.PROCESSING
    assert "Whether the presentation reached the verifier is unknown" in str(exc)
    assert trail["stage"] == progress.MATCH


def test_the_request_still_up_after_share_is_gataca_asking_again(phone):
    _request_until_share(phone, [("offer", None)])
    exc, _ = _raised(_verify, phone)
    assert exc.category == outcome.PROMPT_LOOP


def test_the_trail_of_a_verification_that_stops_at_the_request(phone):
    phone.show(("error", None))
    exc, trail = _raised(_verify, phone)
    assert exc.category == outcome.REJECTED
    assert "could not process the verification request" in str(exc)
    assert trail["stage"] == progress.OPEN


# --- "Sending your consent" lies over home ------------------------------------------------------

@pytest.mark.parametrize("ending, category, stage", [
    ("rejected", outcome.REJECTED, progress.MATCH),
    ("complete", None, progress.ACCEPT),
])
def test_verification_does_not_pass_on_the_home_under_the_consent_overlay(phone, ending, category,
                                                                          stage):
    """2.5.9 dumps: 'Sending your consent' over home, whose heading stays in the tree. Home won,
    the flow returned, and a later Rejected or Verification Complete was never seen."""
    _request_until_share(phone, [("auth", None)])
    phone.then["auth"] = [("processing_home", 4.0), (ending, None)]
    if category:
        exc, trail = _raised(_verify, phone)
        assert exc.category == category and trail["stage"] == stage
    else:
        _verify(phone)
        assert progress.snapshot()["stage"] == stage


def test_a_consent_that_never_finishes_is_processing_in_verification(phone):
    _request_until_share(phone, [("auth", None)])
    phone.then["auth"] = [("processing_home", None)]
    exc, trail = _raised(_verify, phone)
    assert exc.category == outcome.PROCESSING and "still on 'Sending your consent'" in str(exc)
    assert trail["stage"] == progress.OPEN or trail["stage"] == progress.MATCH


@pytest.mark.parametrize("ending, category", [("error", outcome.REJECTED), ("shared", None)])
def test_issuance_does_not_pass_on_the_home_under_the_consent_overlay(phone, ending, category):
    phone.show(("offer", None))
    phone.then = {"Share": [("auth", None)], "auth": [("processing_home", 4.0), (ending, None)],
                  "OK": {"shared": [("home", None)]}}
    if category:
        exc, _ = _raised(_issue, phone)
        assert exc.category == category
    else:
        _issue(phone)
        assert progress.snapshot()["stage"] == progress.ACCEPT


def test_a_consent_that_never_finishes_is_processing_in_issuance(phone):
    phone.show(("offer", None))
    phone.then = {"Share": [("auth", None)], "auth": [("processing_home", None)]}
    exc, _ = _raised(_issue, phone)
    assert exc.category == outcome.PROCESSING and "still on 'Sending your consent'" in str(exc)


# --- the no-match dialog in issuance ---------------------------------------------------------------

def test_no_match_in_issuance_is_worded_for_an_issuer_not_a_verifier(phone):
    phone.show(("no_match", None))
    exc, _ = _raised(_issue, phone)
    assert exc.category == outcome.NO_MATCH and str(exc).endswith("[no_retry]")
    assert "verifier" not in str(exc) and "sharing requirements" in str(exc)
    assert wallet_said(exc) == NO_MATCH_BODY


def test_a_missing_email_under_the_no_match_dialog_is_ours_not_the_wallets(phone):
    phone.show(("no_match_email", None))
    exc, _ = _raised(_issue, phone)
    assert exc.category == outcome.SETUP and "Email credential not available" in str(exc)


# --- the active DID ------------------------------------------------------------------------------

def test_a_did_that_cannot_be_made_active_is_setup_with_the_old_wording(monkeypatch):
    aliases = iter(["Gataca", "Gataca"])
    monkeypatch.setattr(setup_flow, "HomePage",
                        lambda d, **kw: SimpleNamespace(active_did_alias=lambda: next(aliases)))
    monkeypatch.setattr(setup_flow, "ensure_did", lambda *a, **k: False)
    monkeypatch.setattr(time, "sleep", lambda s: None)
    with pytest.raises(outcome.FlowFailure) as exc:
        setup_flow.ensure_active_did(object(), app_package=PACKAGE, did_method="jwk")
    assert exc.value.category == outcome.SETUP
    assert "Active DID is 'Gataca', expected 'JWK Identity' for did_method 'jwk'" in str(exc.value)


def test_a_did_that_is_active_or_can_be_switched_to_lets_the_test_go_on(monkeypatch):
    switched = []
    aliases = iter(["JWK Identity"])
    monkeypatch.setattr(setup_flow, "HomePage",
                        lambda d, **kw: SimpleNamespace(active_did_alias=lambda: next(aliases)))
    monkeypatch.setattr(setup_flow, "ensure_did", lambda *a, **k: switched.append(1))
    assert setup_flow.ensure_active_did(object(), app_package=PACKAGE, did_method="jwk") is True
    assert switched == []

    aliases = iter(["Gataca", "JWK Identity"])
    monkeypatch.setattr(setup_flow, "HomePage",
                        lambda d, **kw: SimpleNamespace(active_did_alias=lambda: next(aliases)))
    monkeypatch.setattr(time, "sleep", lambda s: None)
    assert setup_flow.ensure_active_did(object(), app_package=PACKAGE, did_method="jwk") is False
    assert switched == [1]


def test_an_unsupported_did_method_is_a_typed_setup_failure():
    with pytest.raises(outcome.FlowFailure) as exc:
        setup_flow.ensure_did(object(), app_package=PACKAGE, did_method="nope")
    assert exc.value.category == outcome.SETUP and "Unsupported did_method 'nope'" in str(exc.value)


# --- init_flow and setup_flow, typed -------------------------------------------------------------

@pytest.mark.parametrize("raised, category", [
    (PinFieldMissing("no field"), outcome.SETUP),
    (TimeoutException("Message: "), outcome.SETUP),
    (NoSuchElementException("//x"), outcome.SETUP),
    (RuntimeError("[page] something our own page said"), outcome.SETUP),
    (Exception("Element ('xpath', '//x') not clickable after 10s"), outcome.SETUP),
    (DEAD, outcome.LOST_SESSION),
])
def test_setup_flow_types_whatever_its_pages_and_the_driver_raise(monkeypatch, raised, category):
    def boom(*a, **k):
        raise raised
    monkeypatch.setattr(setup_flow, "_ensure_did", boom)
    with pytest.raises(outcome.FlowFailure) as exc:
        setup_flow.ensure_did(object(), app_package=PACKAGE, did_method="jwk")
    assert exc.value.category == category


def _init(monkeypatch, *, state="home", auth=None, open_button=True):
    monkeypatch.setattr(init_flow, "authenticate_device",
                        auth or (lambda *a, **k: False))
    monkeypatch.setattr(init_flow, "_detect_state", lambda d: state)
    monkeypatch.setattr(init_flow, "has_open_wallet_button", lambda d, *a, **k: open_button)
    return SimpleNamespace(current_package=PACKAGE)


class _UnlockScreens:
    """A driver whose screen after the unlock prompt is `first`, then home once opened."""

    def __init__(self, first):
        self.screen = first

    def find_elements(self, by, value):
        wanted = {init_flow._home_id: "home", init_flow._landing_id: "landing"}.get((by, value))
        return [object()] if wanted == self.screen else []


def test_an_unlock_that_lands_on_open_your_wallet_goes_on_to_home(monkeypatch):
    # gataca 2.6.5: the in-app unlock prompt can end on the "Open your wallet" welcome screen.
    driver = _UnlockScreens("landing")
    opened = []

    def unlock_from_landing(d, page_args, pin, timeout):
        opened.append(pin)
        d.screen = "home"
        return True

    monkeypatch.setattr(init_flow, "authenticate_device", lambda *a, **k: True)
    monkeypatch.setattr(init_flow, "has_open_wallet_button", lambda d, *a, **k: True)
    monkeypatch.setattr(init_flow, "_unlock_from_landing", unlock_from_landing)
    init_flow._unlock_biometric(driver, "1234", 1, {})
    assert opened == ["1234"]


def test_an_unlock_whose_open_your_wallet_never_reaches_home_is_processing(monkeypatch):
    monkeypatch.setattr(init_flow, "authenticate_device", lambda *a, **k: True)
    monkeypatch.setattr(init_flow, "has_open_wallet_button", lambda d, *a, **k: True)
    monkeypatch.setattr(init_flow, "_unlock_from_landing", lambda *a, **k: False)
    with pytest.raises(outcome.FlowFailure) as exc:
        init_flow._unlock_biometric(_UnlockScreens("landing"), "1234", 1, {})
    assert exc.value.category == outcome.PROCESSING
    assert "Open your wallet" in str(exc.value)


def test_an_unlock_that_reaches_home_directly_does_not_open_the_wallet(monkeypatch):
    monkeypatch.setattr(init_flow, "authenticate_device", lambda *a, **k: True)
    monkeypatch.setattr(init_flow, "_unlock_from_landing",
                        lambda *a, **k: pytest.fail("home needs no 'Open your wallet'"))
    init_flow._unlock_biometric(_UnlockScreens("home"), "1234", 1, {})


def test_init_flow_on_the_landing_screen_without_open_wallet_is_setup(monkeypatch):
    driver = _init(monkeypatch, state="landing", open_button=False)
    with pytest.raises(outcome.FlowFailure) as exc:
        init_flow.run(driver, pin="1", app_package=PACKAGE)
    assert exc.value.category == outcome.SETUP and "manual onboarding required" in str(exc.value)


def test_init_flow_with_a_phone_sheet_that_has_no_pin_field_is_setup(monkeypatch):
    def auth(*a, **k):
        raise PinFieldMissing("[android] PIN entry field did not appear after switching to PIN")
    driver = _init(monkeypatch, auth=auth)
    with pytest.raises(outcome.FlowFailure) as exc:
        init_flow.run(driver, pin="1", app_package=PACKAGE)
    assert exc.value.category == outcome.SETUP and "offers only a fingerprint" in str(exc.value)


def test_init_flow_with_a_dead_session_is_lost_session(monkeypatch):
    def auth(*a, **k):
        raise DEAD
    driver = _init(monkeypatch, auth=auth)
    with pytest.raises(outcome.FlowFailure) as exc:
        init_flow.run(driver, pin="1", app_package=PACKAGE)
    assert exc.value.category == outcome.LOST_SESSION


def test_init_flow_wiping_the_app_without_skip_is_setup(monkeypatch):
    driver = _init(monkeypatch, state="home")
    driver.execute_script = lambda *a, **k: None
    driver.terminate_app = driver.activate_app = lambda *a, **k: None
    with pytest.raises(outcome.FlowFailure) as exc:
        init_flow.run(driver, pin="1", skip_if_done=False, app_package=PACKAGE)
    assert exc.value.category == outcome.SETUP and "manual re-onboarding" in str(exc.value)


def test_a_screen_no_probe_knows_after_a_restart_is_setup_and_a_crash_dialog_is_crashed(monkeypatch):
    class Driver:
        current_package = PACKAGE
        back = terminate_app = activate_app = lambda self, *a: None
    monkeypatch.setattr(init_flow, "_detect_state", lambda d: "unknown")
    monkeypatch.setattr(init_flow, "_dismiss_outcome_screen", lambda d: False)
    monkeypatch.setattr(failures, "detect_crash_or_anr", lambda d, *a, **k: None)
    with pytest.raises(outcome.FlowFailure) as exc:
        init_flow._back_to_known_state(Driver(), PACKAGE)
    assert exc.value.category == outcome.SETUP

    monkeypatch.setattr(failures, "detect_crash_or_anr", lambda d, *a, **k: SystemOverlay.ANR)
    with pytest.raises(outcome.FlowFailure) as exc:
        init_flow._back_to_known_state(Driver(), PACKAGE)
    assert exc.value.category == outcome.CRASHED


class _Dismissable(Screen):
    """A dump whose screen goes away once a button on it has been tapped."""

    def find_elements(self, by, value):
        return [] if self.tapped else super().find_elements(by, value)


def test_teardown_dismisses_the_error_screen_a_flow_left_up_instead_of_pressing_back(monkeypatch):
    """The flows no longer tap Go back before raising; this is where the next start recovers."""
    dump = _Dismissable(error_screen(AUTHBOUND_HEADING))
    presses = []
    dump.back = lambda: presses.append("back")
    monkeypatch.setattr(init_flow, "_detect_state",
                        lambda d: "home" if d.tapped else "unknown")
    assert init_flow._back_to_known_state(dump, PACKAGE) == "home"
    assert dump.tapped == ["Go back"] and presses == []


def test_teardown_taps_ok_on_a_rejected_dialog_a_flow_left_up(monkeypatch):
    dump = _Dismissable(REJECTED)
    dump.back = lambda: pytest.fail("a dialog with an OK is not backed out of")
    monkeypatch.setattr(init_flow, "_detect_state",
                        lambda d: "home" if d.tapped else "unknown")
    assert init_flow._back_to_known_state(dump, PACKAGE) == "home"
    assert dump.tapped == ["OK"]


def test_no_gataca_flow_reaches_a_bare_runtime_error():
    """Every raise in the flows is a FlowFailure; a page object's own error can still escape."""
    from pathlib import Path
    root = Path(__file__).resolve().parents[2] / "wallets/gataca/flows"
    for path in sorted(root.glob("*.py")):
        assert not re.search(r"raise (RuntimeError|ValueError)\(", path.read_text()), path.name


# --- the list at its top, the fingerprint sheet, the loading state (gataca 2.6.5, 2026-11-19) ------

def test_a_list_at_the_top_is_never_scrolled_up_whatever_it_holds():
    # Scrolling up at the top pulls gataca's list to refresh, which asks for a fingerprint: it
    # did, in every count of reports/2026-11-19_10-51-00/gataca.
    for cards in (_oma(1), [("Self-attested, Device Id, 356c94c51da403ef", 562)], _oma(5), _kela(12)):
        wallet = Wallet(cards)
        assert _home(wallet).count_credentials() == len(cards)
        assert wallet.sheets_raised == 0
        assert all(start > 0 for start in wallet.up_starts), "no up-gesture started at the top"


def test_no_up_gesture_is_sent_when_the_first_read_is_at_the_top():
    wallet = Wallet(_oma(1))
    _home(wallet).count_credentials()
    assert [g for g in wallet.gestures if g["direction"] == "up"] == []


def test_a_list_scrolled_down_is_brought_back_to_the_top_without_an_overscroll():
    wallet = Wallet(_oma(6))
    assert _home(wallet).count_credentials() == 6
    assert wallet.sheets_raised == 0
    assert all(start > 0 for start in wallet.up_starts), "no up-gesture started at the top"
    ups = [i for i, g in enumerate(wallet.gestures) if g["direction"] == "up"]
    assert ups and wallet.offset <= 8, "back at the top, give or take the tolerance"


def _sheet_after_first_gesture(wallet):
    """Whatever the gesture was, the fingerprint sheet is up after the first one."""
    real = wallet.execute_script

    def gesture(script, args):
        out = real(script, args)
        if script == "mobile: scrollGesture" and len(wallet.gestures) == 1:
            wallet.sheet = True
        return out
    wallet.execute_script = gesture
    return wallet


def test_the_fingerprint_sheet_after_a_gesture_is_answered_and_the_count_completes(answers):
    wallet = _sheet_after_first_gesture(Wallet(_oma(5)))
    assert _home(wallet, default=10).count_credentials() == 5
    assert answers == ["9876"], "answered once, with the device PIN to hand"
    assert not wallet.sheet


def test_an_unanswerable_sheet_is_unavailable_with_the_honest_reason(monkeypatch, clock):
    wallet = _sheet_after_first_gesture(Wallet(_oma(5)))
    monkeypatch.setattr(home_page, "authenticate_device", lambda *a, **k: False)
    with pytest.raises(CredentialCountUnavailable,
                       match="pulling gataca's list to its top asks for a fingerprint, which was "
                             "not answered"):
        _home(wallet).count_credentials()


def test_a_sheet_that_stays_up_after_the_answer_is_unavailable(monkeypatch, clock):
    wallet = _sheet_after_first_gesture(Wallet(_oma(5)))
    monkeypatch.setattr(home_page, "authenticate_device", lambda *a, **k: True)
    with pytest.raises(CredentialCountUnavailable, match="fingerprint, which was not answered"):
        _home(wallet).count_credentials()


def test_a_sheet_the_device_cannot_answer_is_unavailable_not_a_crash(monkeypatch, clock):
    wallet = _sheet_after_first_gesture(Wallet(_oma(5)))

    def refuse(*a, **k):
        raise PinFieldMissing("no PIN field")
    monkeypatch.setattr(home_page, "authenticate_device", refuse)
    with pytest.raises(CredentialCountUnavailable, match="fingerprint, which was not answered"):
        _home(wallet).count_credentials()


def test_the_loading_state_is_waited_out_and_the_cards_are_counted(clock):
    wallet = Wallet(_oma(5), loading=4)
    assert _home(wallet, default=10).count_credentials() == 5
    assert wallet.reads > 4


def test_a_loading_state_that_shows_its_cards_after_two_seconds_is_not_an_empty_wallet(clock):
    # "No credentials yet" for 2.0s (four polls), under the 3s it must stay to be believed.
    assert _home(Wallet(_kela(3), loading=5), default=10).count_credentials() == 3


def test_no_credentials_yet_that_stays_is_zero_credentials(clock):
    wallet = Wallet([], scrollable=False, empty_state=True)    # the chips stay, as on 2.6.5
    assert _home(wallet, default=10).count_credentials() == 0
    assert wallet.gestures == []
    assert clock[0] - 1000 >= 3, "believed only once it has stayed ~3s"


def test_no_credentials_yet_from_the_start_of_a_short_wait_is_still_zero(clock):
    assert _home(Wallet([], scrollable=False, empty_state=True)).count_credentials() == 0


def test_no_list_and_no_empty_text_is_unavailable(clock):
    with pytest.raises(CredentialCountUnavailable, match="neither a credentials list nor"):
        _home(Wallet([], scrollable=False), default=10).count_credentials()
    assert clock[0] - 1000 >= 10, "it waited its whole time before giving up"


def test_the_real_samples_are_read_as_loading_and_as_the_sheet():
    # Minimal structures from the Appium page sources of 2026-11-19 (vanished_1, vanished_2).
    loading = screen(tv("My credentials"), button("All"), button("Favorites"),
                     tv("Sorry!", bounds="[469,1399][612,1462]"),
                     tv("No credentials yet", bounds="[374,1483][707,1536]"))
    assert home_page.read_page(loading) is None and "No credentials yet" in loading
    assert home_page.sheet_up(FINGERPRINT_SHEET) and not home_page.sheet_up(loading)


def test_at_top_is_the_first_card_26px_under_the_list_edge_and_nowhere_else():
    P = home_page.Page
    C = home_page.Card
    assert home_page.at_top(P(517, 2153, [C("x", 543, 1105)]))          # the measured single card
    assert not home_page.at_top(P(517, 2153, [C("x", 560, 1105)]))      # scrolled 17 down: not yet
    assert not home_page.at_top(P(517, 2153, [C("x", 518, 700)]))       # clipped at the top: +1
    assert not home_page.at_top(P(517, 2153, [C("x", 543 + 40, 1105)]))
    assert not home_page.at_top(P(517, 2153, [])), "nothing to read a top from"


# --- the banner that moves the page, and a list that all fits (2026-11-19 11:33) -------------------

def test_a_count_waits_out_the_issued_banner_and_sends_no_gesture_during_the_move(clock):
    wallet = Wallet([("OmaKela Pensiondemo", 562), ("Self-attested, Device Id, x", 562)], banner=6)
    assert _home(wallet, default=10).count_credentials() == 2
    assert wallet.gestures == []
    assert wallet.reads > 6, "it read the list only once the banner was gone"


def test_a_list_that_keeps_moving_is_unavailable(clock):
    wallet = Wallet(_oma(2), moving=True)
    with pytest.raises(CredentialCountUnavailable, match="still moving"):
        _home(wallet, default=10).count_credentials()


@pytest.mark.parametrize("n", [1, 2])
def test_cards_that_all_fit_are_counted_with_no_scroll_gesture(n):
    wallet = Wallet([("OmaKela Pensiondemo", 562)] * n)
    assert _home(wallet).count_credentials() == n
    assert wallet.gestures == []


def test_three_cards_with_the_third_clipped_still_scroll():
    wallet = Wallet(_oma(3))
    assert _home(wallet).count_credentials() == 3
    assert any(g["direction"] == "down" for g in wallet.gestures)


def test_a_list_that_disappears_while_home_settles_is_unavailable_not_empty(monkeypatch):
    # Cards were there a moment ago: losing the list now is "could not look". A 0 here would
    # publish a good issuance as not stored.
    home = _home(Wallet(_oma(2)))
    monkeypatch.setattr(home_page.HomePage, "_settle", lambda self: None)
    with pytest.raises(CredentialCountUnavailable, match="disappeared while its home settled"):
        home.count_credentials()


# --- review round: the invariant, the empty text at the deadline, the toggle, the landing ----------

class _Timed:
    """A driver whose page source depends on the virtual clock: `(until, source)` segments."""
    capabilities: dict = {}
    current_package = PACKAGE

    def __init__(self, clock, segments, last):
        self.clock, self.segments, self.last = clock, segments, last

    @property
    def page_source(self):
        t = self.clock[0] - 1000
        for until, source in self.segments:
            if t < until:
                return source
        return self.last

    def find_elements(self, by, value):
        return Screen(self.page_source).find_elements(by, value)

    def find_element(self, by, value):
        found = self.find_elements(by, value)
        if not found:
            raise NoSuchElementException(value)
        return found[0]


_EMPTY_HOME = screen(tv("My credentials"), button("All"), button("Favorites"),
                     tv("Sorry!"), tv("No credentials yet"))


def test_no_credentials_yet_that_only_just_appeared_at_the_deadline_is_not_zero(clock):
    # 9.5 s of a screen that is neither, then the empty text for the last half second.
    driver = _Timed(clock, [(9.5, screen(tv("My credentials"), tv("Loading")))], _EMPTY_HOME)
    with pytest.raises(CredentialCountUnavailable, match="neither a credentials list nor"):
        _home(driver, default=10).count_credentials()


def test_no_credentials_yet_for_three_seconds_before_the_deadline_is_zero(clock):
    driver = _Timed(clock, [(2.0, screen(tv("My credentials"), tv("Loading")))], _EMPTY_HOME)
    assert _home(driver, default=10).count_credentials() == 0


def test_a_scrollable_list_with_no_cards_is_unavailable_not_zero(clock):
    with pytest.raises(CredentialCountUnavailable, match="showed no cards"):
        _home(Wallet([], scrollable=True)).count_credentials()


def test_a_last_card_within_a_card_gap_of_the_bottom_edge_still_scrolls():
    # Its bottom is 30 px above the list's: another card could hide in the 52 px gap below it.
    wallet = Wallet([("A", 764), ("B", 764)])
    assert _home(wallet).count_credentials() == 2
    assert any(g["direction"] == "down" for g in wallet.gestures)


def test_the_return_to_the_top_asks_for_no_more_than_what_is_left():
    wallet = Wallet(_oma(6))
    _home(wallet).count_credentials()
    ups = [g["percent"] * 1636 for g in wallet.gestures if g["direction"] == "up"]
    assert ups and sum(ups) <= 2048 and wallet.sheets_raised == 0


def test_a_count_whose_return_to_the_top_fails_is_still_the_count(monkeypatch):
    wallet = Wallet(_oma(6))
    home = _home(wallet)
    real = home._to_top

    def to_top(page, remaining=None):
        if remaining is not None:
            raise CredentialCountUnavailable("gataca: the credentials list vanished while "
                                             "scrolling to its top")
        return real(page, remaining)
    monkeypatch.setattr(home, "_to_top", to_top)
    assert home.count_credentials() == 6


def test_a_view_button_pair_inside_the_list_is_not_the_toggle():
    inside = _attrs("android.widget.ScrollView", scrollable=True, bounds="[0,517][1080,2153]") + ">" \
        + _attrs("android.view.ViewGroup", bounds="[42,900][305,1000]") + ">" \
        + _attrs("android.view.ViewGroup", clickable=True, bounds="[47,900][168,990]") + "/>" \
        + _attrs("android.view.ViewGroup", clickable=True, bounds="[178,900][299,990]") + "/>" \
        + "</android.view.ViewGroup></android.widget.ScrollView>"
    assert home_page.list_view_toggle(screen(inside)) is None


def test_an_unlock_that_lands_on_a_landing_without_open_your_wallet_is_setup(monkeypatch):
    monkeypatch.setattr(init_flow, "authenticate_device", lambda *a, **k: True)
    monkeypatch.setattr(init_flow, "has_open_wallet_button", lambda d, *a, **k: False)
    monkeypatch.setattr(init_flow, "_unlock_from_landing",
                        lambda *a, **k: pytest.fail("there is no button to open the wallet with"))
    with pytest.raises(outcome.FlowFailure) as exc:
        init_flow._unlock_biometric(_UnlockScreens("landing"), "1234", 1, {})
    assert exc.value.category == outcome.SETUP and "manual onboarding required" in str(exc.value)
