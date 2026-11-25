import logging
import re
import time
import xml.etree.ElementTree as ET
from collections import namedtuple

from appium.webdriver.common.appiumby import AppiumBy
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, WebDriverException

from base import outcome
from base.android import authenticate_device
from base.base_page import BasePage
from base.credential_count import CredentialCountUnavailable
from base.outcome import FlowFailure
from base.utils import wait_present

logger = logging.getLogger(__name__)

# "My credentials" heading is always visible on the home screen.
SCREEN_ID = (AppiumBy.XPATH, '//*[@text="My credentials"]')

# Credential cards live only inside the *scrollable* vertical credentials list — an
# android.widget.ScrollView with scrollable="true". Scoping to it is essential: the outer
# (non-scrollable) ScrollViews also contain the header "Add" button, the "JWK Identity" picker,
# and (via a HorizontalScrollView) the "All"/"Favorites" chips, while "Scan" and the bottom-nav
# tabs sit outside the list. A plain //ScrollView match grabbed all of those — which made
# count_credentials over-count and made pruning click "Add" (opening "Create a credential")
# instead of a credential. Matching the exact class android.widget.ScrollView (not
# HorizontalScrollView) keeps the All/Favorites chips out. A card's content-desc may be a bare
# issuer name with no comma (e.g. "Kela"), so we only require it be non-empty.
_LIST = (AppiumBy.XPATH, '//android.widget.ScrollView[@scrollable="true"]')
_credential_card = (AppiumBy.XPATH,
    '//android.widget.ScrollView[@scrollable="true"]'
    '//android.view.ViewGroup[@clickable="true" and string-length(@content-desc) > 0]'
)

# The wallet's own device credential — must never be pruned. Its card content-desc starts with this.
_SELF_ATTESTED_PREFIX = "Self-attested"

# Top-left button on home showing the active DID's alias (e.g. "JWK Identity", "Gataca").
# It's the only Button with a non-empty content-desc that isn't the "Add" action.
_DID_ALIAS_BTN = (AppiumBy.XPATH,
    '//android.widget.Button[@clickable="true" and @content-desc!="" and @content-desc!="Add"]'
)


def on_screen(driver, timeout: float = 2) -> bool:
    """gataca's home screen: "My credentials" is showing.

    Also true while a modal such as "Verification Complete" sits over home, since the heading is
    still in the tree beneath it: a caller that cares must probe the modal first.
    """
    return wait_present(driver, SCREEN_ID, timeout=timeout)


# --- counting every card, by scrolling ------------------------------------------------------------
#
# Android only puts the cards that are laid out into the accessibility tree: ~2.4 of them fit in
# the list ([0,517][1080,2153], 1636 px) and a third is cut at its bottom edge. Counting the nodes
# saturated at 3 (README, procivis 3 -> 3 on 2026-11-18), so a good issuance read as "not stored".
# The cards ARE there, listed under the active DID, so the count scrolls the list a little at a
# time and counts each card once.
#
# Telling the cards apart is the hard part: several share a title ("OmaKela Pensiondemo" x3) and
# a card's *position* is all that separates them. Positions are measured in the page's own frame, so
# two readings are joined by the distance the list moved between them. That distance comes from
# matching cards (same content-desc, same offset from each other) and is only unambiguous when the
# list moved less than the spacing of identical cards — hence steps of 70% of the smallest card
# pitch seen on the page, never the whole viewport.
#
# INVARIANT: every count starts with the list at its top, and every scroll WE make is undone by the
# distance we know we moved (the offset count_cards accumulates), so the list is back at its top when
# we are done. Nothing is ever scrolled up from the top: gataca refreshes a list pulled past its top
# and the refresh raises the system fingerprint sheet. That the list is at its top cannot be read
# off the page — a card scrolled wholly out of the top leaves no node, so "the first visible card is
# 26 px under the list" also holds at offsets of one card pitch more — which is why the invariant
# rather than a look decides; `at_top` is only a sanity check for a list we did not leave ourselves.

Card = namedtuple("Card", "desc top bottom")
Page = namedtuple("Page", "top bottom cards")     # cards: only those whose top edge is visible

_BOUNDS = re.compile(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]")
_TOL = 6          # px: two edges this close are the same edge
_EDGE = 8         # px: a card whose top is this close to an edge of the list may or may not be listed
_SPEED = 1200     # px/s of the scroll gesture; slow, so it does not fling past the step
_MAX_PAGES = 200
_MAX_TO_TOP = 15
# The first card's top edge sits this far under the list's top edge when the list is at its top
# (measured 2026-11-19 on 2.6.5: 543 vs 517 = 26). A card scrolled partly out of the top is clipped
# to the list's edge and reads +0..+1, so it never lands in this window.
_TOP_GAP = (20, 34)
_EMPTY_TEXT = "No credentials yet"
_EMPTY_SETTLE = 3.0   # s: how long "No credentials yet" must stay before it is believed
_POLL = 0.5
_BANNER = "Credential Successfully Issued"
_ROOM = 58            # px: card gap (52) + _TOL; a last card this far above the list's bottom edge
                      # is not clipped by it and nothing hides below
_SHEET_WORDS = ("Perform biometric verification", "biometric_icon")


def _bounds(node):
    m = _BOUNDS.fullmatch(node.get("bounds", ""))
    return tuple(int(v) for v in m.groups()) if m else None


def read_page(source: str):
    """The scrollable credentials list in a page source: `Page(top, bottom, cards)`, or None when
    the screen has none. `cards` are those whose top edge is inside the list — a card scrolled
    partly out of the top has its top clipped to the list's and tells nothing about where it is."""
    try:
        root = ET.fromstring(source)
    except ET.ParseError as exc:
        raise CredentialCountUnavailable(f"gataca: the page source could not be read: {exc}") from exc
    for node in root.iter("android.widget.ScrollView"):
        if node.get("scrollable") != "true":
            continue
        box = _bounds(node)
        if box is None:
            continue
        cards = []
        for card in node.iter("android.view.ViewGroup"):
            cbox = _bounds(card)
            desc = card.get("content-desc") or ""
            if card.get("clickable") == "true" and desc and cbox and cbox[1] > box[1] + 1:
                cards.append(Card(desc, cbox[1], cbox[3]))
        cards.sort(key=lambda c: c.top)
        return Page(box[1], box[3], cards)
    return None


def at_top(page: Page) -> bool:
    """The list is at its top: its first visible card is where the first card rests (26 px under
    the list's top edge). Scrolling up from here pulls gataca's list to refresh, which raises the
    system fingerprint sheet — so nothing may scroll up once this is true. Ambiguous (see the
    invariant above), so a sanity check only. A list with no cards is not at its top."""
    if not page.cards:
        return False
    return _TOP_GAP[0] <= page.cards[0].top - page.top <= _TOP_GAP[1]


def _steady(a: Page, b: Page) -> bool:
    """Two readings of the list with the list and every card in the same place."""
    return (abs(a.top - b.top) <= _TOL and len(a.cards) == len(b.cards)
            and all(abs(x.top - y.top) <= _TOL for x, y in zip(a.cards, b.cards)))


def sheet_up(source: str) -> bool:
    """The Android system biometric sheet is what the page source shows."""
    return any(word in source for word in _SHEET_WORDS)


def list_view_toggle(source: str):
    """Where to tap for gataca's list view, as (x, y), or None when the toggle is not showing.

    Home has two unlabelled view buttons left of the All/Favorites chips: a stacked "deck" view
    (cards drawn over each other, each reported only by its visible strip) and the list view the
    count needs. Their group is the only ViewGroup with exactly two clickable children that carry
    no content-desc; the list view is the second. Measured 2026-11-19 on 2.6.5: [47,412][168,496]
    stacked, [178,412][299,496] list. The view is a remembered setting, so anyone can leave it on
    the deck — which is what broke the count on 2026-11-19.
    """
    try:
        root = ET.fromstring(source)
    except ET.ParseError:
        return None
    listing = read_page(source)
    list_top = listing.top if listing else None
    for group in root.iter("android.view.ViewGroup"):
        gbox = _bounds(group)
        if list_top is not None and (gbox is None or gbox[3] > list_top):
            continue                # the toggle sits above the list, never inside it
        plain = [c for c in group if c.tag == "android.view.ViewGroup"
                 and c.get("clickable") == "true" and not c.get("content-desc")]
        if len(plain) == 2:
            box = _bounds(plain[1])
            if box:
                return (box[0] + box[2]) // 2, (box[1] + box[3]) // 2
    return None


def _same_page(a: Page, b: Page) -> bool:
    """The same cards at the same places, give or take `_TOL`: a last scroll of a few pixels
    (content a hair taller than the list) is the same page, not a move to line up."""
    return len(a.cards) == len(b.cards) and all(
        x.desc == y.desc and abs(x.top - y.top) <= _TOL and abs(x.bottom - y.bottom) <= _TOL
        for x, y in zip(a.cards, b.cards))


def _step_px(page: Page) -> float:
    """How far to scroll next: 70% of the smallest card pitch on this page, within 5%-50% of the
    list. Smaller than any gap between identical cards, so the match is unambiguous."""
    height = page.bottom - page.top
    pitches = [b.top - a.top for a, b in zip(page.cards, page.cards[1:])]
    pitches += [c.bottom - c.top for c in page.cards if c.bottom < page.bottom - 1]
    pitches = [p for p in pitches if p > 0]
    step = 0.7 * min(pitches) if pitches else 0.25 * height
    return max(0.05 * height, min(0.5 * height, step))


def shift_between(prev: Page, new: Page, expected: float, at_end: bool = False):
    """How far the list moved between two readings, in px, or None when no distance explains
    both. Candidates are the offsets that line one card up with a card of the same content-desc;
    one is accepted only if EVERY card that should be visible in both readings lines up.

    When identical cards repeat several offsets fit, and `expected` (what the gesture was asked to
    move) decides: the nearest one, within 60%. When none is that close and several fit, the answer
    is unknowable and a count that is a guess is CredentialCountUnavailable. A single fit is
    unambiguous, however far off the gesture was.
    """
    def lined_up(shift):
        seen = 0
        for n in new.cards:
            q = n.top + shift                       # where it was in the previous reading
            if prev.top + _EDGE < q < prev.bottom - _EDGE:
                if not any(p.desc == n.desc and abs(p.top - q) <= _TOL for p in prev.cards):
                    return False
                seen += 1
        for p in prev.cards:
            r = p.top - shift                       # where it is in the new reading
            if new.top + _EDGE < r < new.bottom - _EDGE:
                if not any(n.desc == p.desc and abs(n.top - r) <= _TOL for n in new.cards):
                    return False
        return seen > 0

    candidates = sorted({p.top - n.top for p in prev.cards for n in new.cards
                         if p.desc == n.desc and p.top - n.top > _TOL})
    fits = [s for s in candidates if lined_up(s)]
    if not fits:
        return None
    # Every wrong fit differs from the true one by at least the spacing of identical cards, which
    # is more than the steps are, so the fit nearest `expected` is the one — if it is within 60% of
    # it. (Real gestures lose ~20% to margins and touch slop.) A gesture that stopped at the end of
    # the list moved less than asked, so then anything up to 1.6x will do.
    low = 0.0 if at_end else 0.4 * expected
    close = [s for s in fits if low <= s <= 1.6 * expected]
    if close:
        return min(close, key=lambda s: abs(s - expected))
    if len(fits) == 1:
        return fits[0]
    raise CredentialCountUnavailable(
        f"gataca: the list moved by one of {fits}px, but {expected:.0f}px was asked; with "
        "identical cards there is no telling which, so the count would be a guess")


def count_cards(pages) -> int:
    """The number of distinct cards across consecutive readings of the list, from the start."""
    return count_cards_and_offset(pages)[0]


def count_cards_and_offset(pages):
    """`(cards, offset)`: the number of distinct cards across consecutive readings of the list,
    and how far, in px, the last reading is below the first. `pages` is an iterable of (page,
    expected step, ended there); the first is the top of the list."""
    unique, offset, prev = [], 0.0, None
    for page, expected, at_end in pages:
        if prev is not None:
            shift = shift_between(prev, page, expected, at_end)
            if shift is None:
                raise CredentialCountUnavailable(
                    "gataca: two readings of the credentials list could not be lined up")
            offset += shift
        for card in page.cards:
            top = card.top + offset
            if not any(u[0] == card.desc and abs(u[1] - top) <= _TOL for u in unique):
                unique.append((card.desc, top))
        prev = page
    return len(unique), offset


class HomePage(BasePage):
    def __init__(self, driver, device_pin=None, **config):
        super().__init__(driver, **config)
        self.device_pin = device_pin
        self._refreshed = False

    def wait_until_loaded(self):
        try:
            WebDriverWait(self.driver, self._get_timeout("default")).until(
                EC.presence_of_element_located(SCREEN_ID)
            )
        except TimeoutException:
            raise FlowFailure(outcome.PROCESSING, (
                f"[gataca] Its home screen ('My credentials') did not show within "
                f"{self._get_timeout('default'):.0f}s, so nothing could be read from it. "
                "Whatever is in front is not a screen this framework recognises"
            ))

    # -- the list ------------------------------------------------------------------------------

    def _source(self) -> str:
        """The page source, with the system biometric sheet answered if it is what is showing.

        Gataca refreshes its list when it is pulled past its top, and the refresh asks for a
        fingerprint over the app. The sheet is answered (a fingerprint on an emulator, the device
        PIN on a phone) and the home is read again; one that cannot be answered ends the count.
        """
        source = self.driver.page_source
        if not sheet_up(source):
            return source
        self._refreshed = True
        reason = ("gataca: pulling gataca's list to its top asks for a fingerprint, which was "
                  "not answered")
        logger.info("[gataca] The system fingerprint sheet is up over the list — answering it")
        try:
            handled = authenticate_device(self.driver, self.device_pin)
        except (RuntimeError, WebDriverException) as exc:
            raise CredentialCountUnavailable(f"{reason} ({exc})") from exc
        if not handled:
            raise CredentialCountUnavailable(reason)
        deadline = time.monotonic() + self._get_timeout("default")
        while True:
            source = self.driver.page_source
            if not sheet_up(source):
                return source
            if time.monotonic() >= deadline:
                raise CredentialCountUnavailable(reason)
            time.sleep(_POLL)

    def _page(self):
        page = read_page(self._source())
        if page is None and self._refreshed:
            # The sheet was just answered and the list is reloading: wait for it, not for a vanish.
            self._refreshed = False
            return self._wait_for_list()
        self._refreshed = False
        return page

    def _settle(self):
        """The credentials list once gataca's home has stopped moving.

        Right after an issuance a green "Credential Successfully Issued" banner pushes the whole
        page down ~130 px for a second or two, then it slides back. A count read during that move
        cannot be lined up. So: the banner must be gone and two reads 0.5 s apart must agree on the
        list's top and every card's top. Returns None if the list is gone (the caller treats
        that as unavailable, not empty).
        """
        timeout = self._get_timeout("default")
        deadline = time.monotonic() + timeout
        prev = None
        while True:
            source = self._source()
            page = read_page(source)
            if page is None:
                return None
            if _BANNER in source:
                prev = None
            else:
                if prev is not None and _steady(prev, page):
                    return page
                prev = page
            if time.monotonic() >= deadline:
                raise CredentialCountUnavailable(
                    f"gataca's home was still moving after {timeout:.0f}s (the issued banner or a "
                    "slide), so the credentials list could not be read at rest")
            time.sleep(_POLL)

    def _wait_for_list(self):
        """The credentials list once it has cards, or None for a wallet that holds nothing.

        Right after the unlock gataca briefly shows "Sorry! / No credentials yet" with the chips
        and no list, and seconds later the same DID shows its cards (2.6.5, 2026-11-19). So the
        empty text is believed only once it has stayed `_EMPTY_SETTLE` seconds (or for the whole
        wait). A screen with neither a list nor that text is not an answer: Unavailable.
        """
        timeout = self._get_timeout("default")
        start = time.monotonic()
        deadline = start + timeout
        empty_since, listing = None, None
        while True:
            source = self._source()
            page = read_page(source)
            now = time.monotonic()
            if page is not None:
                if page.cards:
                    return page
                listing, empty_since = page, None
            else:
                listing = None
                if _EMPTY_TEXT in source:
                    empty_since = now if empty_since is None else empty_since
                    if now - empty_since >= _EMPTY_SETTLE:
                        return None
                else:
                    empty_since = None
            if now >= deadline:
                break
            time.sleep(_POLL)
        if listing is not None:
            raise CredentialCountUnavailable(
                f"gataca: the credentials list showed no cards and no '{_EMPTY_TEXT}' within "
                f"{timeout:.0f}s, so a count would mean 'could not look', not 'wallet is empty'")
        if empty_since is not None and (empty_since == start or now - empty_since >= _EMPTY_SETTLE):
            return None
        raise CredentialCountUnavailable(
            f"gataca: neither a credentials list nor '{_EMPTY_TEXT}' showed within "
            f"{timeout:.0f}s, so a count would mean 'could not look', not 'wallet is empty'")

    def _scroll(self, direction: str, px: float, height: float):
        """One scroll gesture on the credentials list. Returns the driver's "can scroll more"."""
        element = self.driver.find_element(*_LIST)
        percent = max(0.02, min(1.0, px / height)) if height else 0.25
        return self.driver.execute_script("mobile: scrollGesture", {
            "elementId": getattr(element, "id", None), "direction": direction,
            "percent": round(percent, 3), "speed": _SPEED,
        })

    def _select_list_view(self):
        """Tap gataca's list-view button, so the cards are laid out one under another. Tapping
        it when the list view is already on changes nothing; without the toggle there is nothing
        to choose and the count goes on with whatever is shown."""
        spot = list_view_toggle(self._source())
        if spot is None:
            logger.info("[gataca] View toggle not found — counting the list as shown")
            return
        logger.info(f"[gataca] Tapping the list-view button at {spot}")
        self.driver.execute_script("mobile: clickGesture", {"x": spot[0], "y": spot[1]})
        time.sleep(0.5)  # the list re-lays out its cards

    def _to_top(self, page: Page, remaining=None) -> Page:
        """Scroll the list back to its first card; the page that was found there. Never scrolls
        up from the top (that pulls the list to refresh, and the refresh asks for a fingerprint).

        `remaining` is how far below its top the list is, when we know (we scrolled it down
        ourselves): each step then asks for no more than what is left, so none overshoots. Without
        it (a list we did not leave) the steps are small and stop at the first sanity-check top.
        """
        height = page.bottom - page.top
        for _ in range(_MAX_TO_TOP):
            if remaining is None:
                if at_top(page):
                    return page
                ask = 0.1 * height
            else:
                # Under the smallest gesture there is nothing left to ask for: it would overshoot.
                if remaining <= max(_TOL, 0.02 * height) or (
                        at_top(page) and remaining < 0.1 * height):
                    return page
                ask = min(_step_px(page), remaining - _TOL / 2)   # a hair short, never past the top
            self._scroll("up", ask, height)
            now = self._page()
            if now is None:
                raise CredentialCountUnavailable("gataca: the credentials list vanished while "
                                                 "scrolling to its top")
            if remaining is None:
                if _same_page(now, page):
                    return now
            else:
                try:
                    moved = shift_between(now, page, ask, at_end=True)
                except CredentialCountUnavailable:
                    moved = None
                remaining -= moved if moved else 0.8 * ask    # unknown: assume it fell short
            page = now
        return page

    def visible_cards(self) -> int:
        """The cards currently laid out on screen — NOT the wallet's count: it stops at what fits.
        What cleanup falls back on when scrolling cannot give a count."""
        return len(self.driver.find_elements(*_credential_card))

    def count_credentials(self) -> int:
        """Return the number of credentials the active DID holds, scrolling to see them all.

        Cards are on the home screen, so counting needs no navigation — but it does need the
        home screen to actually be showing: anywhere else the card locator matches nothing,
        which is indistinguishable from an empty wallet. Credentials are listed per active DID,
        so the caller makes sure the DID the credential goes to is the active one first.

        Leaves the list scrolled to its top. Raises CredentialCountUnavailable only when the
        scrolling cannot be completed or the readings cannot be lined up.
        """
        if not wait_present(self.driver, SCREEN_ID, timeout=self._get_timeout("default")):
            raise CredentialCountUnavailable(
                "gataca: 'My credentials' is not showing, so a count would mean "
                "'could not look', not 'wallet is empty'"
            )
        try:
            first = self._wait_for_list()
            if first is None:
                return 0
            self._select_list_view()
            first = self._wait_for_list()      # the view change lays the cards out again
            if first is None:
                return 0
            first = self._settle()
            if first is None:
                # The list had cards a moment ago; losing it now is "could not look", never
                # "empty" — 0 here would publish a good issuance as not stored.
                raise CredentialCountUnavailable(
                    "gataca: the credentials list disappeared while its home settled")
            if first.cards and at_top(first) and first.cards[-1].bottom <= first.bottom - _ROOM:
                return len(first.cards)       # every card is on screen: nothing to scroll for
            top = self._to_top(first)
            pages, page = [(top, 0.0, False)], top
            for _ in range(_MAX_PAGES):
                step = _step_px(page)
                more = self._scroll("down", step, page.bottom - page.top)
                now = self._page()
                if now is None:
                    raise CredentialCountUnavailable("gataca: the credentials list vanished while "
                                                     "scrolling")
                if _same_page(now, page):
                    break
                pages.append((now, step, more is False))
                page = now
                if more is False:
                    break
            else:
                raise CredentialCountUnavailable(
                    f"gataca: the credentials list was still scrolling after {_MAX_PAGES} steps")
            total, offset = count_cards_and_offset(pages)
            try:
                self._to_top(page, remaining=offset)
            except (CredentialCountUnavailable, WebDriverException) as exc:
                logger.warning(f"[gataca] The count is {total}, but the list could not be scrolled "
                               f"back to its top ({exc})")
            return total
        except CredentialCountUnavailable:
            raise
        except WebDriverException as e:
            raise CredentialCountUnavailable(f"gataca: scrolling the credential list failed: {e}") from e

    def active_did_alias(self) -> str:
        """Return the active DID's alias shown on the home top-left button (e.g. 'JWK Identity').

        Waits for the button to render — reading it immediately after navigating home can otherwise
        return "" before the home screen has finished drawing, causing false DID-mismatch failures.
        """
        try:
            el = WebDriverWait(self.driver, self._get_timeout("default")).until(
                EC.presence_of_element_located(_DID_ALIAS_BTN)
            )
            return el.get_attribute("content-desc") or ""
        except TimeoutException:
            return ""

    def open_credential(self) -> bool:
        """Open the first credential card that is safe to delete (i.e. not the self-attested
        device credential), scrolling down for it if it is not on screen. Returns True if one was
        opened, False if none remain."""
        try:
            page = self._page()
            page = self._to_top(page) if page else page
        except CredentialCountUnavailable:
            page = None
        for _ in range(_MAX_PAGES):
            for card in self.driver.find_elements(*_credential_card):
                desc = card.get_attribute("content-desc") or ""
                if not desc.startswith(_SELF_ATTESTED_PREFIX):
                    card.click()
                    return True
            if page is None:
                return False
            more = self._scroll("down", _step_px(page) * 2, page.bottom - page.top)
            now = self._page()
            if now is None or _same_page(now, page):
                return False
            page = now
            if more is False:
                # The last scroll moved the list: look once more at where it stopped.
                for card in self.driver.find_elements(*_credential_card):
                    desc = card.get_attribute("content-desc") or ""
                    if not desc.startswith(_SELF_ATTESTED_PREFIX):
                        card.click()
                        return True
                return False
        return False
