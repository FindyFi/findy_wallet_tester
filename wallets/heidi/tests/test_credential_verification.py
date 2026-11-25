import importlib
import logging
import pytest
from pathlib import Path

from base import credential_count, outcome
from base.test_cases import verification_cases
from providers.factory import get_provider
from wallets.heidi.pages.home_page import HomePage

logger = logging.getLogger(__name__)

# APP_NAME is derived from the parent directory so this file works unchanged in any wallet.
APP_NAME = Path(__file__).parents[1].name
verification_flow = importlib.import_module(f"wallets.{APP_NAME}.flows.verification_flow")
_verification_cases = verification_cases(APP_NAME)


@pytest.mark.parametrize("driver", [APP_NAME], indirect=True)
@pytest.mark.parametrize("issuer_name,test_case", _verification_cases)
def test_credential_verification(app, issuer_name, test_case):
    pin = app.config["application"]["pin"]
    app_package = app.config["application"]["package"]

    # An empty wallet and a wallet holding the wrong credential both end on heidi's "No Matching
    # Credentials", and they point at different parties: the first means issuance failed earlier
    # in this run, the second is a disagreement with this verifier. Counted first, with the same
    # call the issuance test makes, so the two cannot be published as one. An unreadable count
    # leaves the decision to the flow, which then reports `no_match`.
    held = credential_count.read(HomePage(app.driver, **app.page_args), when="before verification")
    if held == 0:
        outcome.fail(outcome.NOTHING_TO_PRESENT, (
            f"Cannot verify '{test_case}' against '{issuer_name}': heidi holds no credentials, so "
            "nothing could be shared. If issuance ran earlier in this run, it left nothing behind."
        ))

    provider = get_provider(app.config, issuer_name)
    verification_flow.run(
        app.driver,
        provider=provider,
        credential_name=test_case,
        app_package=app_package,
        pin=pin,
        **app.page_args,
    )
    logger.info(f"[test] Verification '{test_case}' from '{issuer_name}' completed")
