"""Which outcome screens gataca has, for the shared failure diagnosis in base/outcome.py.

gataca's own probes, each anchored on captured dumps (the page modules say which): the
2.6.5 run reports/2026-11-18_12-11-52/gataca and the 2.5.9 runs of 2026-06 to 2026-09. Not a copy
of any other wallet's probes — base/screens.py says why a copied probe is worse than none.

gataca's flows keep their own waits; they do not call `outcome.wait_for`, whose interstitial
service swallows a handler's exception (a SETUP failure of the system auth sheet would be logged
and lost). What this declaration buys is the rest of the shared machinery: the device-backed
`unroutable` diagnosis, a failure message that says which surfaces were actually looked for, and
the app.log digest quoting gataca's own words.
"""
from base.screens import Screens
from wallets.gataca.pages import credential_offer_page, error_page
from wallets.gataca.pages.home_page import on_screen as _home_on_screen

SCREENS = Screens(
    name="gataca",
    home=_home_on_screen,

    # The QR-processing error: a heading (gataca's, an untranslated `[missing "en.…"]` key, or
    # paradym's), the shared "The QR you just scanned …" sentence, and Go back.
    error=error_page.present,
    error_text=error_page.message,

    # "Unfortunately, giving consent is not possible" over Sharing requirements.
    no_match=credential_offer_page.no_match,

    # "Sending your consent" over the screen gataca is working on.
    processing=credential_offer_page.processing,

    # Left UNKNOWN deliberately, not claimed absent. "Credentials Shared" ends an issuance and
    # some presentations, and the presentation's own dialog is "Verification Complete" (a modal
    # over the dimmed home list, measured on the device) — but no *saved dump* holds it: a passing
    # test saves none and the screen is FLAG_SECURE, so it has no fixture of its own. The flows
    # probe both by name.
    # success: UNKNOWN
)
