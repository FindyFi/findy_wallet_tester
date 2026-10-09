"""Which outcome screens heidi has, for the shared failure diagnosis in base/outcome.py.

heidi's own page modules, on text locators captured from this wallet: it is Compose with no test
tags, so its screens are anchored on their copy (see wallets/README.md). Not a copy of any other
wallet's probes — base/screens.py says why a copied probe is worse than none.

heidi's flows keep their own polling loops (`_wait_for_result`, `_wait_for_request`,
`_wait_for_share_result`): they already answer the connection-consent screen and the fingerprint
prompt in whatever order heidi raises them. What this declaration buys is the rest of the shared
machinery — the device-backed `unroutable` diagnosis, a failure message that says which surfaces
were actually looked for, and the app.log digest quoting heidi's own error copy.
"""
from base.screens import Screens
from wallets.heidi.pages import error_page, verification_request_page, verification_success_page
from wallets.heidi.pages.home_page import on_screen as _home_on_screen

SCREENS = Screens(
    name="heidi",

    # The dashboard. A broad text anchor — "No Matching Credentials" also contains "Credentials"
    # — which is why every flow checks the request and no-match screens before home.
    home=_home_on_screen,

    # Two error screens, one probe: issuance's "An error occurred." and the verification "Error".
    error=error_page.any_error,
    error_text=error_page.message,

    # Never observed. heidi goes from the connection screen straight to the offer or request;
    # nothing in the captured dumps is a spinner, but absence of evidence is not a claim.
    # processing: left UNKNOWN on purpose.

    no_match=verification_request_page.no_match,

    # "Information Successfully Shared", held until DONE is tapped. Issuance shows no confirmation
    # of its own — it goes straight back to the dashboard — so this is the presentation's screen.
    success=verification_success_page.on_screen,

    # These two only steer `outcome.wait_for`, which heidi does not call yet. Set to what its own
    # loops do: `_HOME_GRACE` in credential_flow ignores the dashboard it was already on for 8 s.
    home_grace=8.0,
    processing_grace=0.0,
)
