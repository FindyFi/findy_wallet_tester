"""Which outcome screens sphereon has, for the shared wait loop in base/outcome.py.

These are sphereon's own page modules, captured on 0.9.0 (build 901) — not copies of another
wallet's. See base/screens.py for why "this wallet has no such screen" (None) and "nobody looked"
(the default, UNKNOWN) have to stay different answers.
"""
from base.screens import Screens
from wallets.sphereon.pages import error_page, loading_page, no_match_page
from wallets.sphereon.pages.home_page import on_screen as _home_on_screen

SCREENS = Screens(
    name="sphereon",
    home=_home_on_screen,
    error=error_page.present,
    error_text=error_page.message,
    processing=loading_page.on_screen,
    no_match=no_match_page.present,

    # Nobody has looked for a success screen. The wallet's own help text says "a confirmation
    # appears upon acceptance", so one probably exists — but no issuance has ever completed here,
    # so claiming None would publish a guess as a fact.
    #   success — unmapped

    # sphereon stays on its home screen for the first second or two after a deeplink before the
    # "Getting information..." screen replaces it. Measured 2026-09-10: home still showing at 1.0s,
    # spinner at 1.9s, trust consent at 4.3s. Without the grace, every case would be called
    # `dismissed` on the first poll.
    home_grace=8.0,

    # The spinner buys a little time, since it is genuinely fetching issuer metadata over the
    # network. Every failure so far arrived within ~3s, so this has never been needed; it is here
    # for a slow issuer rather than to paper over a stuck one.
    processing_grace=15.0,

    # The error screen is a full screen that replaces the flow and lands back on home when
    # dismissed, so one showing at the start of a case cannot be left over from an earlier one.
    error_sticky=False,
)
