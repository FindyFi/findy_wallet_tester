"""Which outcome screens procivis has, so the app.log digest can quote the wallet's own words.

Without this module `_error_surface` in base/conftest_helpers.py reports "unmapped" and every
procivis block in app.log says `wallet said: (unknown — no error screen is mapped for procivis)` —
which it did for all eleven blocks of the 2026-09-21 run, while "Connection failed" sat in the XML
dump beside it.

These are procivis's own probes, not copies of hovi's. See base/screens.py on why a copied probe is
worse than no probe: it matches nothing, returns False forever, and disables error detection without
failing anything.
"""
from base.screens import Screens
from wallets.procivis.pages import credential_accept_process_page as _process
from wallets.procivis.pages.home_page import on_screen as _home_on_screen

SCREENS = Screens(
    name="procivis",
    home=_home_on_screen,

    # The failure animation on either process screen, plus the label beside it. `message` is
    # read-only; the richer Code/Message/Cause is collected by `flows/_failures.rejected` at
    # failure time, where tapping the "?" is safe.
    error=_process.present,
    error_text=_process.message,

    # Left UNKNOWN deliberately, not claimed absent. Every procivis XML dump this repo holds was
    # written by a *failing* test, so the processing, no-match and success surfaces have never been
    # observed. Writing `None` here would claim procivis has no such screen — see base/screens.py
    # on why forgetting must stay noisy and disclaiming must stay deliberate.
)
