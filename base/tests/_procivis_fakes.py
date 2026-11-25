"""Fakes shared by the procivis tests, so no test module imports another.

Not collected by pytest (no `test_` prefix). Imported as a sibling module, the same way pytest's
rootdir insertion already lets these test files import each other.
"""
import re

from selenium.common.exceptions import NoSuchElementException, WebDriverException

from base.conftest_helpers import _QUOTED

URL = "openid-credential-offer://?credential_offer_uri=https%3A%2F%2Fissuer.example"
PACKAGE = "ch.procivis.one.wallet.trial"

# The real shape, from reports/2026-09-24_18-13-04/procivis/appium.log (authbound_verifier).
REFUSED = ("Error attempting to start URI. Original error: Error: Starting: Intent "
           "{ act=android.intent.action.VIEW dat=eudi-openid4vp:///... pkg=ch.procivis.one.wallet.trial }\n"
           "Error: Activity not started, unable to resolve Intent "
           "{ act=android.intent.action.VIEW dat=eudi-openid4vp:///... flg=0x10000000 "
           "pkg=ch.procivis.one.wallet.trial }")

DEAD = WebDriverException("instrumentation process is not running")


def wallet_said(exc) -> str:
    """What the app.log digest would publish as the wallet's words: the first quoted span."""
    m = _QUOTED.search(str(exc))
    return m.group(1) if m else ""


# --- a driver for the flows, whose probes are faked one by one -----------------------------------

class Driver:
    """`front` is what `current_package` reports; `refuse` is what firing the deeplink raises;
    `texts` is what a `//*[@text!=""]` lookup returns; `source` is the page source (an exception to
    raise it). HOME and the app-state query answer as a backgrounded app would."""
    capabilities: dict = {}

    def __init__(self, refuse=None, front=PACKAGE, texts=(), source=None):
        self.refuse = refuse
        self.current_package = front
        self.texts = list(texts)
        self._source = source

    @property
    def page_source(self):
        if isinstance(self._source, BaseException):
            raise self._source
        if self._source is None:
            raise WebDriverException("no page source in this fake")
        return self._source

    def press_keycode(self, code):
        pass

    def query_app_state(self, app_id):
        return 3

    def execute_script(self, script, args):
        if self.refuse:
            raise WebDriverException(self.refuse)

    def find_elements(self, *a):
        return [_Text(t) for t in self.texts]


class _Text:
    def __init__(self, text):
        self.text = text

    def get_attribute(self, name):
        if isinstance(self.text, BaseException):
            raise self.text
        return self.text


class Waits:
    """Stands in for WebDriverWait: each `until` takes the next scripted answer, in order."""

    def __init__(self, *answers):
        self.answers = list(answers)

    def __call__(self, driver, timeout):
        return self

    def until(self, condition):
        answer = self.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return True


class Probes:
    """Stands in for `wait_present`: answers per locator, in turn (the last answer repeats; a
    locator not listed is absent). An exception as an answer is raised. Records what was asked."""

    def __init__(self, answers):
        self.answers = {k: list(v) for k, v in answers.items()}
        self.asked = []

    def __call__(self, driver, locator, timeout=2):
        self.asked.append((locator, timeout))
        seq = self.answers.get(locator, [False])
        answer = seq.pop(0) if len(seq) > 1 else seq[0]
        if isinstance(answer, BaseException):
            raise answer
        return answer


# --- a fake device screen for the page objects ---------------------------------------------------
#
# Deliberately not a general XPath engine. It understands exactly the predicate shapes the procivis
# page objects use — `contains(@resource-id, "x")`, `starts-with(@resource-id, "x")`, the
# `substring(…string-length…) = "x"` suffix test, `@resource-id="x"`, `@text="x"`, `@text!=""`,
# joined by `or` / `and`. A locator written in some other shape will not match here, which is the
# intended failure: this is not the place to discover that a new locator form works on-device.

_ATOMS = (
    (re.compile(r'contains\(@resource-id,\s*"([^"]*)"\)'),
     lambda m, node: m.group(1) in node.get("resource-id", "")),
    (re.compile(r'starts-with\(@resource-id,\s*"([^"]*)"\)'),
     lambda m, node: node.get("resource-id", "").startswith(m.group(1))),
    (re.compile(r'substring\(@resource-id,\s*string-length\(@resource-id\)\s*-\s*\d+\)'
                r'\s*=\s*"([^"]*)"'),
     lambda m, node: node.get("resource-id", "").endswith(m.group(1))),
    (re.compile(r'@resource-id="([^"]*)"'),
     lambda m, node: node.get("resource-id", "") == m.group(1)),
    (re.compile(r'@text!=""'),
     lambda m, node: bool(node.get("text", ""))),
    (re.compile(r'@text="([^"]*)"'),
     lambda m, node: node.get("text", "") == m.group(1)),
)


def _atom_matches(atom: str, node: dict) -> bool:
    for pattern, test in _ATOMS:
        m = pattern.fullmatch(atom.strip())
        if m:
            return test(m, node)
    raise AssertionError(f"the fake screen does not understand this predicate: {atom!r}")


def _matches(xpath: str, node: dict) -> bool:
    predicate = re.fullmatch(r'//\*\[(.*)\]', xpath).group(1)
    return any(
        all(_atom_matches(atom, node) for atom in conjunction.split(" and "))
        for conjunction in predicate.split(" or ")
    )


class FakeElement:
    def __init__(self, driver, node):
        self._driver, self._node = driver, node

    def is_displayed(self):
        return True

    def is_enabled(self):
        return True

    def get_attribute(self, name):
        return self._node.get(name, "")

    def click(self):
        self._driver.tapped.append(self._node.get("resource-id", ""))
        self._driver.nodes = self._driver.on_tap.get(
            self._node.get("resource-id", ""), self._driver.nodes
        )


class FakeDriver:
    """One screen at a time. `on_tap` maps a resource-id to the screen that tapping it opens."""

    current_package = PACKAGE
    capabilities: dict = {}

    def __init__(self, nodes, on_tap=None):
        self.nodes, self.on_tap = list(nodes), dict(on_tap or {})
        self.tapped, self.went_back = [], 0

    def find_elements(self, by, value):
        return [FakeElement(self, n) for n in self.nodes if _matches(value, n)]

    def find_element(self, by, value):
        found = self.find_elements(by, value)
        if not found:
            raise NoSuchElementException(value)
        return found[0]

    def back(self):
        self.went_back += 1


def in_front(driver, screen_id) -> bool:
    return any(n.get("resource-id") == screen_id for n in driver.nodes)


# --- screens, copied from real XML dumps ---------------------------------------------------------

def process_screen(animation: str, label: str = "Connection failed"):
    return [
        {"resource-id": "InvitationProcessScreen"},
        {"resource-id": f"InvitationProcessScreen.animation.{animation}"},
        {"resource-id": "InvitationProcessScreen.header.close", "text": "Close"},
        {"resource-id": "InvitationProcessScreen.header.info"},
        {"resource-id": "InvitationProcessScreen.label", "text": label},
    ]


def details_screen(*, expandable=True, cause=None):
    """InvitationErrorDetailsScreen, as the 2026-10-14 dumps show it."""
    nodes = [
        {"resource-id": "InvitationErrorDetailsScreen"},
        {"resource-id": "InvitationErrorDetailsScreen.code.attributeLabel", "text": "Code"},
        {"resource-id": "InvitationErrorDetailsScreen.code.attributeValue", "text": "BR_0395"},
        {"resource-id": "InvitationErrorDetailsScreen.message.attributeLabel", "text": "Message"},
        {"resource-id": "InvitationErrorDetailsScreen.message.attributeValue",
         "text": "Remote HTTP request status failure (4xx)"},
    ]
    if cause is not None:
        nodes += [
            {"resource-id": "InvitationErrorDetailsScreen.cause.attributeLabel", "text": "Cause"},
            {"resource-id": "InvitationErrorDetailsScreen.cause.attributeValue", "text": cause},
        ]
    if expandable:
        nodes.append({"resource-id": "InvitationErrorDetailsScreen.cause.expandValueButton"})
    return nodes
