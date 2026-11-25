"""toppan's error dialog: a plain Android AlertDialog with toppan's message and one OK button.

Every toppan error this repo and the nightly clone hold has this shape, 2026-08-13 through
2026-10-15 — e.g. PUBLISH_findy_wallet_tester/reports/2026-10-15_22-00-02/toppan/xml_dumps/
test_credential_verification_waltid_verifier_pension_verification-toppan.xml: no title
(`android:id/textSpacerNoTitle`), the copy in `android:id/message`, a single button
`android:id/button1` labelled "OK", every node in toppan's own package.

`button1` alone, which is what this wallet used to match, is the positive button of *every*
AlertDialog: Android's own dialogs have one, and so would a two-button dialog of toppan's (its
issuance and verification confirmations offer CANCEL / CONTINUE). The probe is therefore anchored
on all three things the dumps show together: toppan's package, `button1`, and the label "OK".

Each condition is its own predicate (`[@a="x"][@b="y"]`) rather than an `and`: the same XPath then
runs on-device and in the unit tests, which evaluate it against copies of the captured dumps.

The package is a parameter, never a constant here: the flows pass the `app_package` they were
given (from config.json), so a renamed package changes the probe with it instead of silently
blinding it.
"""
from appium.webdriver.common.appiumby import AppiumBy

from base.utils import wait_present


def _ok(package: str):
    return (AppiumBy.XPATH,
            f'//android.widget.Button[@resource-id="android:id/button1"][@text="OK"]'
            f'[@package="{package}"]')


def _message(package: str):
    return (AppiumBy.XPATH,
            f'//android.widget.TextView[@resource-id="android:id/message"][@package="{package}"]')


def present(driver, timeout: float = 2, *, package: str) -> bool:
    return wait_present(driver, _ok(package), timeout=timeout)


def message(driver, *, package: str) -> str:
    """The dialog's own copy on one line, or "" when no toppan error dialog is up or it cannot be
    read. Never raises.

    One line because toppan's copy can carry a line break — the waltid issuer's
    `JsonDecodingException … at path: $` is followed by "JSON input: " on a second line — and only
    a failure's first line reaches summary.json and the app.log digest.
    """
    try:
        if not driver.find_elements(*_ok(package)):
            return ""
        found = driver.find_elements(*_message(package))
        text = found[0].text if found else ""
    except Exception:
        return ""
    return " ".join((text or "").split())
