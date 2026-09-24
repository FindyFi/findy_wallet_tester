"""procivis publishes Code/Message/Cause behind a "?" — these hold the path that reaches it open.

The evidence behind this file is in the XML dumps every failed procivis test already writes. In the
2026-09-21 run four of five issuance failures reported only "did not complete within timeout", and
their dumps show `InvitationProcessScreen.animation.warning` with `InvitationProcessScreen.header.info`
sitting right there unopened. The gate only recognised `.animation.error`, so the collector below it
was unreachable for the *common* failure.

These tests drive the real `CredentialAcceptProcessPage.wait_for_result` against a fake driver, so
they fail if the gate narrows again, if the details screen stops being opened, or if one missing
button goes back to discarding the whole collection.
"""
import re

import pytest
from selenium.common.exceptions import NoSuchElementException

from wallets.procivis.pages.credential_accept_process_page import CredentialAcceptProcessPage


# --- a fake device screen ----------------------------------------------------------------------
#
# Deliberately not a general XPath engine. It understands exactly the predicate shapes the procivis
# page objects use — `contains(@resource-id, "x")`, `@resource-id="x"`, `@text="x"`, `@text!=""`,
# joined by `or` / `and`. A locator written in some other shape will not match here, which is the
# intended failure: this file is not the place to discover that a new locator form works on-device.

_ATOMS = (
    (re.compile(r'contains\(@resource-id,\s*"([^"]*)"\)'),
     lambda arg, node: arg in node.get("resource-id", "")),
    (re.compile(r'@resource-id="([^"]*)"'),
     lambda arg, node: node.get("resource-id", "") == arg),
    (re.compile(r'@text!=""'),
     lambda arg, node: bool(node.get("text", ""))),
    (re.compile(r'@text="([^"]*)"'),
     lambda arg, node: node.get("text", "") == arg),
)


def _atom_matches(atom: str, node: dict) -> bool:
    for pattern, test in _ATOMS:
        m = pattern.fullmatch(atom.strip())
        if m:
            return test(m.group(1) if m.groups() else None, node)
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


# --- screens, copied from real XML dumps ---------------------------------------------------------

def _process_screen(animation: str, label: str = "Connection failed"):
    return [
        {"resource-id": "InvitationProcessScreen"},
        {"resource-id": f"InvitationProcessScreen.animation.{animation}"},
        {"resource-id": "InvitationProcessScreen.header.close", "text": "Close"},
        {"resource-id": "InvitationProcessScreen.header.info"},
        {"resource-id": "InvitationProcessScreen.label", "text": label},
    ]


def _details_screen(*, expandable=True):
    nodes = [
        {"resource-id": "InvitationErrorDetailsScreen"},
        {"resource-id": "InvitationErrorDetailsScreen.code.attributeLabel", "text": "Code"},
        {"resource-id": "InvitationErrorDetailsScreen.code.attributeValue", "text": "BR_0395"},
        {"resource-id": "InvitationErrorDetailsScreen.message.attributeLabel", "text": "Message"},
        {"resource-id": "InvitationErrorDetailsScreen.message.attributeValue",
         "text": "Remote HTTP request status failure (4xx)"},
    ]
    if expandable:
        nodes.append({"resource-id": "InvitationErrorDetailsScreen.cause.expandValueButton"})
    return nodes


def _page(driver):
    return CredentialAcceptProcessPage(
        driver, timeouts={"credential_offer": 2, "default": 1}, debug=False
    )


@pytest.fixture
def failing_driver():
    def build(animation, *, label="Connection failed", expandable=True):
        return FakeDriver(
            _process_screen(animation, label),
            on_tap={"InvitationProcessScreen.header.info": _details_screen(expandable=expandable)},
        )
    return build


# --- the gap this file exists for ----------------------------------------------------------------

def test_a_warning_animation_opens_the_details_screen(failing_driver):
    """The common failure. Before this, it fell through to the timeout and published nothing."""
    driver = failing_driver("warning")

    with pytest.raises(RuntimeError) as excinfo:
        _page(driver).wait_for_result()

    assert "InvitationProcessScreen.header.info" in driver.tapped, \
        "the '?' was never tapped, so the details screen was never opened"
    assert "BR_0395" in str(excinfo.value)
    assert "Remote HTTP request status failure (4xx)" in str(excinfo.value)
    assert "did not complete within timeout" not in str(excinfo.value)


def test_an_error_animation_still_opens_the_details_screen(failing_driver):
    """The variant that already worked — widening the gate must not have cost it."""
    driver = failing_driver("error")

    with pytest.raises(RuntimeError) as excinfo:
        _page(driver).wait_for_result()

    assert "InvitationProcessScreen.header.info" in driver.tapped
    assert "BR_0395" in str(excinfo.value)


def test_the_issuance_failed_label_still_counts_as_a_failure(failing_driver):
    """CredentialAcceptProcessScreen has only ever shown `.warning` plus this label."""
    driver = failing_driver("warning", label="Issuance failed")

    with pytest.raises(RuntimeError) as excinfo:
        _page(driver).wait_for_result()

    assert "BR_0395" in str(excinfo.value)


def test_a_missing_expand_button_does_not_discard_the_collected_details(failing_driver):
    """Not every error carries an expandable cause; Code and Message are on screen regardless."""
    driver = failing_driver("warning", expandable=False)

    with pytest.raises(RuntimeError) as excinfo:
        _page(driver).wait_for_result()

    assert "no details available" not in str(excinfo.value)
    assert "BR_0395" in str(excinfo.value)


def test_the_failure_returns_to_the_process_screen_so_teardown_can_close_it(failing_driver):
    driver = failing_driver("warning")

    with pytest.raises(RuntimeError):
        _page(driver).wait_for_result()

    assert driver.went_back == 1


def test_no_retry_survives_so_a_read_failure_is_not_retried(failing_driver):
    """`[no_retry]` is what keeps a decided failure from being rerun — see commit a28d0b5."""
    driver = failing_driver("warning")

    with pytest.raises(RuntimeError) as excinfo:
        _page(driver).wait_for_result()

    assert "[no_retry]" in str(excinfo.value)


# --- what app.log publishes --------------------------------------------------------------------
#
# The digest reads the wallet's copy through base/screens.py. Before wallets/procivis/screens.py
# existed, `_error_surface` reported "unmapped" and every procivis block in the 2026-09-21 run said
# `wallet said: (unknown — no error screen is mapped for procivis)` — with the copy sitting in the
# XML dump named on the very next line.

from base.conftest_helpers import _error_surface, _read_error_text  # noqa: E402


def test_procivis_no_longer_reports_an_unmapped_error_surface():
    assert _error_surface("procivis") == "mapped"


@pytest.mark.parametrize("animation,label", [
    ("warning", "Connection failed"),
    ("error", "No credential interaction found."),
    ("warning", "Issuance failed"),
])
def test_the_digest_reads_the_copy_procivis_actually_shows(animation, label):
    """All three strings procivis has ever printed on a process screen, across 75 dumps."""
    driver = FakeDriver(_process_screen(animation, label))
    assert _read_error_text(driver, "procivis") == label


def test_a_processing_screen_is_not_published_as_an_error():
    """The process screen is also what procivis shows while it works — only the animation decides."""
    driver = FakeDriver([
        {"resource-id": "InvitationProcessScreen"},
        {"resource-id": "InvitationProcessScreen.label", "text": "Please wait"},
    ])
    assert _read_error_text(driver, "procivis") == ""


def test_reading_the_copy_never_taps_anything():
    """Teardown must not drive the UI of a screen a test just failed on."""
    driver = FakeDriver(_process_screen("warning"))
    _read_error_text(driver, "procivis")
    assert driver.tapped == []
    assert driver.went_back == 0
