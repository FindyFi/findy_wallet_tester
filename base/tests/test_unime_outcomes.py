"""unime's error surface and its Connection Request screen, against its own captured screens.

Two layers, neither touching a device:

- the **page probes** run for real against screens copied from reports/2026-11-20_14-40-17/unime
  (unime 0.19.1), through a driver that evaluates the page modules' XPath with ElementTree;
- the **flows** run for real against a `Phone` whose screen changes when something is tapped.

unime renders its refusals as a toast over the home screen, so the cases that matter are the ones
where home is in the tree at the same moment: that is what used to publish `dismissed`.
"""
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import replace
from types import SimpleNamespace

import pytest
from appium.webdriver.common.appiumby import AppiumBy
from selenium.common.exceptions import NoSuchElementException

from base import deeplink, interstitials, outcome, progress
from wallets.unime.flows import credential_flow as cf, verification_flow as vf
from wallets.unime.pages import (
    connection_request_page, credential_offer_page, error_page, home_page, pin_page,
    verification_request_page)
from wallets.unime.screens import SCREENS

PACKAGE = "com.impierce.identity_wallet"

# unime's own words, from the dumps.
HEADING = "Couldn't open this request"
HOVI_CAUSE = ("UniMe got an answer from core-agent.hovi.id that it couldn't understand. Contact "
              "them if this keeps happening.")
QR_CAUSE = ("This QR code isn't a request UniMe can handle. Scan a QR code for receiving or "
            "sharing credentials.")
OLD_HEADING = "Oops!"
OLD_MESSAGE = "Something went wrong. Please try again."


def _tv(text):
    return f'<android.widget.TextView text="{text}" package="{PACKAGE}"/>'


def _screen(*texts, extra=""):
    inner = "".join(_tv(t) for t in texts)
    return (f'<hierarchy><android.webkit.WebView package="{PACKAGE}">'
            f'<android.view.View>{inner}{extra}</android.view.View>'
            f'<android.widget.TextView text="untitled page" resource-id="svelte-announcer"/>'
            f'</android.webkit.WebView></hierarchy>')


# Home is still in the tree under the toast, as in the dumps.
HOME_TEXTS = ("ME", "Welcome back, Me.", "Scan", "Activity")
HOME = _screen(*HOME_TEXTS)
TOAST_HOVI = _screen(*HOME_TEXTS, HEADING, HOVI_CAUSE)
TOAST_QR = _screen(*HOME_TEXTS, HEADING, QR_CAUSE)
OLD_ERROR = _screen(*HOME_TEXTS, OLD_HEADING, OLD_MESSAGE)
CONNECTION = _screen("Connection Request", "https://procivis.staging.findy.fi",
                     "Unverified Domain", "New connection", "Accept connection", "Reject")
REQUEST = _screen("Verification Request", "Share credentials", "Reject")
OFFER = _screen("Credential Offer", "Accept credentials", "Reject")


class _Node:
    def __init__(self, phone, node):
        self._phone, self._node = phone, node

    def get_attribute(self, name):
        return self._node.get(name, "")

    def is_displayed(self):
        return True

    def is_enabled(self):
        return True

    def click(self):
        self._phone.tap(self._node.get("text", ""))


class Screen:
    """One screen as a driver. Understands exactly the XPath shapes unime's pages use — a text
    equality, starts-with, a union of those, and one following-sibling step — and fails on any
    other, so a locator written in a shape this cannot read fails here, not by matching nothing."""
    current_package = PACKAGE
    capabilities: dict = {}

    def __init__(self, xml):
        self.xml = xml
        self.tapped = []
        self.then = {}
        self._load(xml)

    def _load(self, xml):
        self.root = ET.fromstring(xml)
        self.parent = {c: p for p in self.root.iter() for c in p}

    def tap(self, label):
        self.tapped.append(label)
        if label in self.then:
            self._load(self.then[label])

    def _one(self, expr):
        m = re.fullmatch(r'//\*\[(@text="([^"]*)"|starts-with\(@text,"([^"]*)"\))\]'
                         r'(?:/following-sibling::android\.widget\.TextView\[1\])?', expr)
        assert m, f"unsupported XPath: {expr}"
        exact, prefix = m.group(2), m.group(3)
        hits = [n for n in self.root.iter()
                if (n.get("text") == exact if exact is not None
                    else n.get("text", "").startswith(prefix))]
        if "following-sibling" not in expr:
            return hits
        out = []
        for n in hits:
            sibs = list(self.parent[n])
            after = sibs[sibs.index(n) + 1:]
            nxt = [s for s in after if s.tag == "android.widget.TextView"][:1]
            out.extend(nxt)
        return out

    def find_elements(self, by, value):
        assert by == AppiumBy.XPATH
        found = []
        for expr in value.split(" | "):
            found.extend(self._one(expr))
        return [_Node(self, n) for n in found]

    def find_element(self, by, value):
        found = self.find_elements(by, value)
        if not found:
            raise NoSuchElementException(value)
        return found[0]


def _fast_wait_present(driver, locator, timeout=2):
    return bool(driver.find_elements(*locator))


@pytest.fixture(autouse=True)
def fast_probes(monkeypatch):
    """`wait_present` polls on the real clock; a fake screen answers at once instead."""
    for module in (error_page, connection_request_page, credential_offer_page, home_page,
                   pin_page, verification_request_page):
        monkeypatch.setattr(module, "wait_present", _fast_wait_present)
    monkeypatch.setattr(outcome, "detect_crash_or_anr", lambda *a, **k: None)
    monkeypatch.setattr(interstitials, "handle_permission_if_present", lambda *a, **k: False)
    monkeypatch.setattr(deeplink, "fire", lambda *a, **k: None)
    monkeypatch.setattr(time, "sleep", lambda s: None)
    progress.reset()
    yield
    progress.reset()


# --- the page probe, against the captured copy ---------------------------------------------------

@pytest.mark.parametrize("xml, said", [
    (TOAST_HOVI, f"{HEADING}: {HOVI_CAUSE}"),
    (TOAST_QR, f"{HEADING}: {QR_CAUSE}"),
    (OLD_ERROR, OLD_MESSAGE),
], ids=["issuer-response", "not-a-request", "old-copy"])
def test_the_error_probe_matches_each_copy_and_reads_what_unime_said(xml, said):
    screen = Screen(xml)
    assert error_page.present(screen)
    assert error_page.message(screen) == said


@pytest.mark.parametrize("xml", [
    HOME, CONNECTION, REQUEST, OFFER,
    _screen(*HOME_TEXTS, OLD_HEADING),                 # "Oops!" alone is not the screen
    _screen(*HOME_TEXTS, OLD_MESSAGE),                 # nor is the generic sentence alone
], ids=["home", "connection-request", "request", "offer", "old-heading-only", "old-message-only"])
def test_the_error_probe_stays_quiet_on_every_other_screen(xml):
    screen = Screen(xml)
    assert not error_page.present(screen)
    assert error_page.message(screen) == ""


def test_a_heading_with_no_cause_line_is_still_the_error_and_quotes_the_heading():
    screen = Screen(_screen(*HOME_TEXTS, HEADING))
    assert error_page.present(screen)
    assert error_page.message(screen) == HEADING


def test_the_cause_is_the_line_after_the_heading_not_any_line_of_the_screen():
    screen = Screen(_screen(HEADING, HOVI_CAUSE, "Scan", "Activity"))
    assert error_page.message(screen) == f"{HEADING}: {HOVI_CAUSE}"


def test_only_the_current_copy_is_kept_from_retrying():
    assert error_page.keeps_no_retry(f"{HEADING}: {HOVI_CAUSE}")
    assert error_page.keeps_no_retry(HEADING)
    assert not error_page.keeps_no_retry(OLD_MESSAGE)
    assert not error_page.keeps_no_retry("")


def test_unime_wires_both_error_hooks_to_the_page():
    assert SCREENS.probe("error") is error_page.present
    assert SCREENS.error_text is error_page.message


# --- the flows -----------------------------------------------------------------------------------

PAGE_ARGS = {"timeouts": {"default": 2, "credential_offer": 1.5}}


def _no_grace(monkeypatch):
    """Skip the home grace for the cases that are not about it, so a verdict costs no 5 seconds."""
    quick = replace(SCREENS, home_grace=0.0)
    monkeypatch.setattr(cf, "SCREENS", quick)
    monkeypatch.setattr(vf, "SCREENS", quick)


def _issue(screen):
    cf.run(screen, provider=SimpleNamespace(get=lambda n: "openid-credential-offer://x"),
           credential_name="credential_issuance", app_package=PACKAGE, **PAGE_ARGS)


def _verify(screen):
    vf.run(screen, provider=SimpleNamespace(get=lambda n: "openid4vp://x"),
           credential_name="pension_verification", app_package=PACKAGE, **PAGE_ARGS)


def _raised(fn, screen):
    with pytest.raises(outcome.FlowFailure) as exc:
        fn(screen)
    return exc.value


@pytest.mark.parametrize("xml, cause", [(TOAST_HOVI, HOVI_CAUSE), (TOAST_QR, QR_CAUSE)],
                         ids=["issuer-response", "not-a-request"])
@pytest.mark.parametrize("flow, run, what, expected", [
    ("credential_flow", _issue, "credential_issuance", "credential offer"),
    ("verification_flow", _verify, "pension_verification", "information request"),
])
def test_the_new_copy_is_rejected_and_quotes_unime_exactly(monkeypatch, xml, cause, flow, run,
                                                           what, expected):
    _no_grace(monkeypatch)
    err = _raised(run, Screen(xml))
    assert err.category == outcome.REJECTED
    assert str(err) == (f"[rejected] [{flow}] unime rejected '{what}': \"{HEADING}: {cause}\". "
                        f"It could not process the {expected} [no_retry]")


def test_the_old_copy_is_still_rejected_and_may_be_retried(monkeypatch):
    _no_grace(monkeypatch)
    err = _raised(_issue, Screen(OLD_ERROR))
    assert err.category == outcome.REJECTED
    assert str(err) == ("[rejected] [credential_flow] unime rejected 'credential_issuance': "
                        f"\"{OLD_MESSAGE}\". It could not process the credential offer")


def test_a_bare_home_screen_is_still_dismissed(monkeypatch):
    _no_grace(monkeypatch)
    err = _raised(_issue, Screen(HOME))
    assert err.category == outcome.DISMISSED


def test_a_toast_that_arrives_just_after_home_is_still_the_refusal():
    """Insurance for the 5s home grace. On 2026-11-20 the three bare-home attempts were cold starts
    where unime dropped the link (no UserError in logcat), so `dismissed` was right for them; in
    every other attempt the refusal was logged 0.6-1.5s after delivery. A slower toast is not
    known to happen, but costs nothing to allow for."""
    screen = Screen(HOME)
    start = time.time()

    class Late(Screen):
        def find_elements(self, by, value):
            if time.time() - start > 0.6 and self.xml is HOME:
                self.xml = TOAST_QR
                self._load(TOAST_QR)
            return super().find_elements(by, value)
    err = _raised(_verify, Late(HOME))
    assert err.category == outcome.REJECTED
    assert QR_CAUSE in str(err)


def test_the_connection_request_is_accepted_and_the_flow_goes_on_to_share(monkeypatch):
    _no_grace(monkeypatch)
    screen = Screen(CONNECTION)
    screen.then = {"Accept connection": REQUEST, "Share credentials": HOME}
    _verify(screen)
    assert screen.tapped == ["Accept connection", "Share credentials"]
    steps = progress.snapshot()["steps"]
    assert "start: answered connection-request (1)" in steps
    assert any("shared for" in s for s in steps)


def test_a_connection_request_that_comes_back_is_not_accepted_forever(monkeypatch):
    _no_grace(monkeypatch)
    screen = Screen(CONNECTION)                       # tapping changes nothing
    err = _raised(_verify, screen)
    assert screen.tapped == ["Accept connection"] * 2
    assert err.category == outcome.PROMPT_LOOP


def test_the_connection_request_is_never_rejected_by_the_flow(monkeypatch):
    _no_grace(monkeypatch)
    screen = Screen(CONNECTION)
    screen.then = {"Accept connection": REQUEST, "Share credentials": HOME}
    _verify(screen)
    assert "Reject" not in screen.tapped
