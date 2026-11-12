"""paradym's error screen: "Something went wrong", a sentence about the card, and its Reason.

Every paradym failure this repo and the nightly clone hold has this shape, 2026-06-22 through
2026-10-27 — e.g. PUBLISH_findy_wallet_tester/reports/2026-10-27_22-00-02/paradym/xml_dumps/
test_credential_issuance_waltid_issuer_credential_issuance-paradym.xml:

    View      text="Something went wrong" heading="true"
    TextView  text="An error occurred while fetching the card information. Ask the issuer …"
    TextView  text="Reason: Error while retrieving credentials\\n\\nDevelopment mode error:\\n…"
    Button    content-desc="Go to wallet,  "

The Reason is one TextView. Its first line is paradym's verdict ("Error while retrieving
credentials", "Credential information could not be extracted", "Presentation information could
not be extracted"). With paradym's Development Mode on, the same TextView goes on with
"Development mode error:" and the underlying cause — sometimes a JSON body, sometimes several
kilobytes of it (paradym_issuer, nightly 2026-09-15). The reports in this repo from 2026-08-27 to
2026-09-18 were taken with it off, and their Reason is the first line alone.

The error screen carries a "Go to wallet" button too, so that button alone is not success; see
result_page.
"""
from appium.webdriver.common.appiumby import AppiumBy

from base.utils import wait_present

# The text alone, not also `@heading="true"`, though every dump carries both: success is "Go to
# wallet" *without* this heading, so a probe that a driver's missing attribute could blind would
# turn every error after Accept or Share into a pass. No other screen in any dump has this text.
HEADING = (AppiumBy.XPATH, '//*[@text="Something went wrong"]')
_REASON = (AppiumBy.XPATH, '//*[starts-with(@text, "Reason:")]')
_DEV_MODE = "Development mode error:"

# How much of the development-mode detail a failure message carries, "…" included. The whole of it
# goes to the log; the message is read in a report tooltip that stops at 200 characters.
_DETAIL_LIMIT = 300


def present(driver, timeout: float = 2) -> bool:
    return wait_present(driver, HEADING, timeout=timeout)


def _one_line(text: str) -> str:
    return " ".join((text or "").split())


def raw_reason(driver) -> str:
    """The Reason TextView exactly as paradym wrote it, or "" when none is on screen. Never
    raises."""
    try:
        found = driver.find_elements(*_REASON)
        return (found[0].text or "") if found else ""
    except Exception:
        return ""


def split_reason(raw: str):
    """(verdict, detail) from the Reason text: its first part without the "Reason:" label, and the
    development-mode detail after it, each on one line ("" where absent).

    Double quotes in the detail become single quotes: the app.log digest reads a failure's first
    double-quoted span as what the wallet said, and a JSON body's quotes would otherwise pair up
    with ours.
    """
    text = raw.split(":", 1)[1] if raw.startswith("Reason:") else raw
    verdict, _, detail = text.partition(_DEV_MODE)
    detail = _one_line(detail).replace('"', "'")
    if len(detail) > _DETAIL_LIMIT:
        detail = detail[:_DETAIL_LIMIT - 1].rstrip() + "…"
    return _one_line(verdict), detail


def message(driver) -> str:
    """paradym's verdict from its Reason, or "" when it cannot be read. Never raises."""
    return split_reason(raw_reason(driver))[0]
