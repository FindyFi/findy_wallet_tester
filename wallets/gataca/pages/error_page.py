"""gataca's error screen: a heading, a sentence about the QR, and a "Go back" button.

Three shapes in the nightly dumps and reports/2026-11-18_12-11-52/gataca/xml_dumps (gataca
2.6.5), all with the same body sentence and the same button:

    TextView   text="Error requesting credentials"                          (hovi_issuer)
    TextView   text='[missing "en.PROOF_ALG_NOT_SUPPORTED: DID key algorithm 'EdDSA' is not
                     accepted by the Issuer. Supported: [ES256]" translation]'
                                                               (authbound_issuer; waltid_issuer
                                                                "Cannot read property 'processId'
                                                                of null")
    TextView   text="QR Code is not working"                                 (paradym_issuer)
    TextView   text="The QR you just scanned is unknown and you cannot access its data. …"
               (paradym's: "…is not working. Try to refresh the page or alert the service
                provider that his qr configuration or credentials are broken.")
    ViewGroup  content-desc="Go back"  clickable

The heading is the wallet's own verdict, or — when gataca has no translation for a server error
code — the untranslated key wrapped in `[missing "en.…" translation]`, which holds the actual
reason. So the probe cannot anchor on one heading (the old one matched only the first shape, and
the other three were read as "nothing appeared"): it anchors on the body sentence every shape
shares, with the old heading kept as an alternative.
"""
import logging
import re

from appium.webdriver.common.appiumby import AppiumBy
from selenium.common.exceptions import StaleElementReferenceException

from base.utils import wait_present

logger = logging.getLogger(__name__)

_BODY_START = "The QR you just scanned"
_OLD_HEADING = "Error requesting credentials"

# The body sentence, or the one heading gataca used before 2.6.5's other shapes were seen. Both
# are anchors, not screens: `present` also asks for the Go back button.
SCREEN_ID = (AppiumBy.XPATH,
             f'//*[starts-with(@text, "{_BODY_START}") or @text="{_OLD_HEADING}"]')
_GO_BACK = (AppiumBy.XPATH, '//*[@content-desc="Go back" or @text="Go back"]')

# android.widget.TextView, not any element with text: a Toast ("Issue reading the QR code" over
# hovi's error screen, package com.android.settings) is a different class, and its node goes stale
# while the toast fades — which, matched by `//*[@text]`, made the old reader raise and return "".
_TEXTS = (AppiumBy.XPATH, '//android.widget.TextView[string-length(@text) > 0]')

# gataca's untranslated key: [missing "en.<the server's code or message>" translation]
_MISSING = re.compile(r'^\[missing\s+"en\.(.*)"\s+translation\]$', re.DOTALL)

# How much of the text a failure message carries ("…" included): it is read in a report tooltip
# that stops at 200 characters, and the whole screen goes to the log.
TEXT_LIMIT = 300


def present(driver, timeout: float = 2) -> bool:
    """gataca's error screen: its body sentence (or the old heading) *and* its Go back button."""
    if not wait_present(driver, SCREEN_ID, timeout=timeout):
        return False
    return bool(driver.find_elements(*_GO_BACK))


def unwrap(heading: str) -> str:
    """`[missing "en.X" translation]` as X; any other heading unchanged. One line, and no double
    quotes: the app.log digest reads a failure's first double-quoted span as what the wallet said,
    and the key's own quotes would otherwise pair up with ours."""
    heading = " ".join((heading or "").split())
    m = _MISSING.match(heading)
    text = m.group(1) if m else heading
    return text.replace('"', "'").strip()


def compose(texts) -> str:
    """What gataca said, from the screen's texts in order: its heading, then the shared body.

    The heading is dropped when it is only "Error requesting credentials", which says nothing the
    body does not. The "Go back" button's own text is never part of it.
    """
    clean = []
    for text in texts:
        text = " ".join((text or "").split())
        if text and text != "Go back" and text not in clean:
            clean.append(text)
    body = next((t for t in clean if t.startswith(_BODY_START)), "")
    heading = next((t for t in clean if t != body), "")
    parts = []
    if heading and heading != _OLD_HEADING:
        parts.append(unwrap(heading))
    if body:
        parts.append(unwrap(body))
    said = ". ".join(p.rstrip(". ") for p in parts if p)
    if len(said) > TEXT_LIMIT:
        said = said[:TEXT_LIMIT - 1].rstrip() + "…"
    return said


def message(driver) -> str:
    """What gataca's error screen said, or "" when it cannot be read. Never raises.

    Read element by element, each in its own try: one node going stale (a toast fading over the
    screen) costs that node, not the whole reading — the old reader returned "" for hovi's second
    and third attempts for exactly that.
    """
    try:
        elements = driver.find_elements(*_TEXTS)
    except Exception as exc:
        logger.warning(f"[gataca] Could not read the error screen: {exc}")
        return ""
    texts = []
    for element in elements:
        try:
            texts.append(element.get_attribute("text") or "")
        except StaleElementReferenceException:
            continue
        except Exception as exc:
            logger.warning(f"[gataca] Could not read a text on the error screen: {exc}")
    return compose(texts)


def go_back_now(driver) -> bool:
    """Tap Go back if the error screen is up, with no wait. True if tapped. Never raises."""
    try:
        found = driver.find_elements(*_GO_BACK)
        if found and driver.find_elements(*SCREEN_ID):
            found[0].click()
            return True
    except Exception:
        pass
    return False
