"""Which outcome screens authbound has, for the shared failure diagnosis in base/outcome.py.

Every probe is authbound's own page module, on locators captured from this wallet — not a copy of
hovi's or anyone else's (base/screens.py says why a copied probe is worse than none). Nearly every
screen here carries a stable `io.authbound.wallet:id/…` resource-id, which is what makes all five
surfaces declarable.

authbound's flows keep their own polling loops (`_wait_for_result`, `wait_for_outcome`) rather
than `outcome.wait_for`: those loops already tell error, offer, home and timeout apart, and their
per-document authentication budget is authbound-specific. What this declaration buys is the rest of
the shared machinery — the device-backed `unroutable` diagnosis in `raise_for(ABSENT)`, a failure
message that says which surfaces were actually looked for, and the app.log digest quoting the
wallet's own error copy.
"""
from base.screens import Screens
from wallets.authbound.pages import document_success_page, error_page, verification_request_page
from wallets.authbound.pages.home_page import on_screen as _home_on_screen

SCREENS = Screens(
    name="authbound",
    home=_home_on_screen,

    # The generic error screen, `content_error_root`: "An unexpected error has occurred…", "Close".
    error=error_page.on_screen,
    error_text=error_page.message,

    # "Please wait…" while a transaction is in flight.
    processing=document_success_page.busy,

    # The request screen's visible empty state.
    no_match=verification_request_page.no_match,

    # The end-of-transaction screen both directions share ("You have successfully added…" /
    # "You successfully shared…").
    success=document_success_page.on_screen,

    # These two only steer `outcome.wait_for`, which authbound does not call yet. Set to what its
    # own loops do, so adopting the shared loop later does not silently change the timing.
    # `_wait_for_result` treats the dashboard as a verdict as soon as it is seen after the deeplink.
    home_grace=0.0,
    # `wait_for_outcome` lets "Please wait…" extend the wait by up to 180 s: issuance and remote
    # presentations genuinely take that long.
    processing_grace=180.0,
)
