"""paradym's archive step when a toast sits on the header icon.

The toast ("Credential not found", live-region, full width) lingered over 50 s on the AVD and
swallowed the tap on the archive icon, so "Yes, archive" never appeared. Two layers, no device:

- the toast's Close locator is evaluated with ElementTree against nodes trimmed from that dump
  (nightly 2026-11-05, paradym 1.23.0) and from the review screen, which has a Close of its own;
- `archive()` runs for real against scripted probes, a tap recorder and a virtual clock.
"""
import xml.etree.ElementTree as ET

import pytest

from base.base_page import BasePage
from base.tests._procivis_fakes import Probes
from wallets.paradym.pages import credential_detail_page as cdp

PAGE = cdp.CredentialDetailPage

# Trimmed from the card details dump: the header's two unlabelled Buttons (back, archive), then
# the toaster region -> toast -> "Credential not found" -> Close.
CARD_DETAILS = """<hierarchy>
<android.widget.TextView text="Card details" content-desc="" bounds="[100,90][600,180]"/>
<android.widget.Button text="" content-desc="" clickable="true" bounds="[0,83][105,188]"/>
<android.widget.Button text="" content-desc="" clickable="true" bounds="[933,83][1038,188]"/>
<android.view.ViewGroup content-desc="Notifications alt+T" bounds="[0,76][1080,2400]">
 <android.view.ViewGroup clickable="true" bounds="[0,81][1080,308]">
  <android.view.View content-desc="Credential not found" bounds="[0,81][1080,308]">
   <android.view.ViewGroup bounds="[947,163][1000,226]">
    <android.widget.Button text="" content-desc="Close" clickable="true"
                           bounds="[947,163][1000,226]"/>
   </android.view.ViewGroup>
  </android.view.View>
 </android.view.ViewGroup>
</android.view.ViewGroup>
</hierarchy>"""

# The verification "Review the request" screen's Close, outside any toaster region.
REVIEW_SCREEN = """<hierarchy>
<android.widget.TextView text="Review the request" bounds="[33,300][690,400]"/>
<android.widget.Button text="" content-desc="Close" clickable="true"
                       bounds="[33,1399][690,1497]"/>
</hierarchy>"""


def _find(xml, locator):
    return ET.fromstring(xml).findall("." + locator[1])


def test_toast_close_matches_only_the_toasts_button():
    found = _find(CARD_DETAILS, cdp._TOAST_CLOSE)
    assert [n.get("bounds") for n in found] == ["[947,163][1000,226]"]


def test_toast_close_ignores_the_review_screens_close():
    assert _find(REVIEW_SCREEN, cdp._TOAST_CLOSE) == []


def test_archive_is_the_last_unlabelled_button():
    """Premise check: the dump's header buttons are in the order `_ARCHIVE`'s [last()] assumes."""
    unlabelled = [n for n in ET.fromstring(CARD_DETAILS).iter()
                  if n.tag == "android.widget.Button" and not n.get("content-desc")]
    assert unlabelled[-1].get("bounds") == "[933,83][1038,188]"


# --- archive() ----------------------------------------------------------------------------------

class Clock:
    """Virtual time; `sleep` raises after `limit` calls so an unbounded wait fails, not hangs."""

    def __init__(self, limit=50):
        self.now = 0.0
        self.sleeps = 0
        self.limit = limit

    def monotonic(self):
        return self.now

    def sleep(self, s):
        self.sleeps += 1
        if self.sleeps > self.limit:
            raise AssertionError("waited without a bound")
        self.now += s


class Setup:
    def __init__(self, monkeypatch, answers, click_errors=None):
        self.probes = Probes(answers)
        self.clock = Clock()
        self.clicks = []
        errors = click_errors or {}

        def click(page, locator, timeout=None, attempts=3):
            self.clicks.append((locator, timeout))
            if locator in errors:
                raise errors[locator]

        monkeypatch.setattr(cdp, "wait_present", self.probes)
        monkeypatch.setattr(cdp, "time", self.clock)
        monkeypatch.setattr(BasePage, "click", click)
        self.page = PAGE(object(), timeouts={"default": 10})

    @property
    def tapped(self):
        return [c[0] for c in self.clicks]

    def timeouts(self, locator):
        return [t for loc, t in self.probes.asked if loc == locator]


def test_no_toast_taps_archive_then_confirm_after_a_cheap_probe(monkeypatch):
    s = Setup(monkeypatch, {cdp._CONFIRM: [True]})
    s.page.archive()
    assert s.tapped == [cdp._ARCHIVE, cdp._CONFIRM]
    assert s.timeouts(cdp._TOAST_CLOSE)[0] <= 1
    assert s.clock.sleeps == 0


def test_toast_is_closed_before_the_archive_tap(monkeypatch):
    s = Setup(monkeypatch, {cdp._TOAST_CLOSE: [True, False], cdp._CONFIRM: [True]})
    s.page.archive()
    assert s.tapped == [cdp._TOAST_CLOSE, cdp._ARCHIVE, cdp._CONFIRM]
    assert s.clicks[0] == (cdp._TOAST_CLOSE, cdp._TOAST_TAP_S)
    assert cdp._TOAST_TAP_S <= 2


def test_close_tap_that_fails_does_not_stop_the_archive(monkeypatch):
    s = Setup(monkeypatch, {cdp._TOAST_CLOSE: [True, False], cdp._CONFIRM: [True]},
              click_errors={cdp._TOAST_CLOSE: Exception("not clickable")})
    s.page.archive()
    assert s.tapped == [cdp._TOAST_CLOSE, cdp._ARCHIVE, cdp._CONFIRM]


def test_toast_that_never_goes_is_named_and_the_wait_is_bounded(monkeypatch):
    s = Setup(monkeypatch, {cdp._TOAST_CLOSE: [True], cdp._CONFIRM: [False]})
    with pytest.raises(RuntimeError, match="toast") as e:
        s.page.archive()
    assert "\"" not in str(e.value)
    assert s.clock.now <= cdp._TOAST_GONE_S + cdp._TOAST_POLL_S
    assert cdp._CONFIRM not in s.tapped
    assert s.tapped == [cdp._TOAST_CLOSE, cdp._ARCHIVE]


def test_missing_sheet_without_a_toast_does_not_blame_the_toast(monkeypatch):
    s = Setup(monkeypatch, {cdp._CONFIRM: [False]})
    with pytest.raises(RuntimeError) as e:
        s.page.archive()
    assert "toast" not in str(e.value)
    assert "\"" not in str(e.value)
    assert s.tapped == [cdp._ARCHIVE]
    assert s.timeouts(cdp._CONFIRM) == [10]


def test_toast_that_goes_after_a_few_polls_is_waited_for(monkeypatch):
    s = Setup(monkeypatch, {cdp._TOAST_CLOSE: [True, True, True, False]})
    assert s.page._clear_toast() is False
    assert s.clock.sleeps == 2


def test_toast_that_stays_is_waited_out_for_the_full_bound(monkeypatch):
    s = Setup(monkeypatch, {cdp._TOAST_CLOSE: [True]})
    assert s.page._clear_toast() is True
    assert cdp._TOAST_GONE_S <= s.clock.now <= cdp._TOAST_GONE_S + cdp._TOAST_POLL_S
