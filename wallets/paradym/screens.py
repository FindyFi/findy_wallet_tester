"""Which outcome screens paradym has, for the shared failure diagnosis in base/outcome.py.

paradym's own probes, each anchored on captured dumps (the page modules say which). Not a copy of
any other wallet's probes — base/screens.py says why a copied probe is worse than none.

paradym's flows keep their own waits; they do not call `outcome.wait_for`. What this declaration
buys is the rest of the shared machinery: the device-backed `unroutable` diagnosis, a failure
message that says which surfaces were actually looked for, and the app.log digest quoting
paradym's own Reason.
"""
from base.screens import Screens
from wallets.paradym.pages import error_page, verification_request_page
from wallets.paradym.pages.home_page import on_screen as _home_on_screen

SCREENS = Screens(
    name="paradym",
    home=_home_on_screen,

    # "Something went wrong", with paradym's Reason beneath it.
    error=error_page.present,
    error_text=error_page.message,

    # "You don't have the required cards" on the review screen, in place of Share.
    no_match=verification_request_page.no_match,

    # Left UNKNOWN deliberately, not claimed absent. No dump shows either. paradym's loading text
    # ("Fetching information") has never been captured, and its success screen is on screen only
    # in tests that pass, which save no dump. A screen recording (nightly 2026-10-27,
    # igrant_playground_pid_sdjwt_verifier) shows it reading "Success!", but a video carries no
    # attributes to anchor on. The flows match success on its "Go to wallet" button with no error
    # heading beside it (see pages/result_page.py).
    # processing: UNKNOWN
    # success: UNKNOWN
)
