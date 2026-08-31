import importlib
import logging
import pytest
from pathlib import Path

from base.test_cases import issuance_cases
from providers.factory import get_provider
from wallets.hovi.flows import outcome
from wallets.hovi.pages.home_page import HomePage

logger = logging.getLogger(__name__)

# APP_NAME is derived from the parent directory so this file works unchanged in any wallet.
APP_NAME = Path(__file__).parents[1].name
credential_flow = importlib.import_module(f"wallets.{APP_NAME}.flows.credential_flow")
_issuance_cases = issuance_cases(APP_NAME)


@pytest.mark.parametrize("driver", [APP_NAME], indirect=True)
@pytest.mark.parametrize("issuer_name,test_case", _issuance_cases)
def test_credential_issuance(app, issuer_name, test_case):
    pin = app.config["application"]["pin"]
    app_package = app.config["application"]["package"]

    home = HomePage(app.driver, **app.page_args)
    count_before = home.count_credentials()

    provider = get_provider(app.config, issuer_name)
    credential_flow.run(
        app.driver,
        provider=provider,
        credential_name=test_case,
        app_package=app_package,
        pin=pin,
        **app.page_args,
    )

    home.wait_until_loaded()
    count_after = home.count_credentials()
    added = count_after - count_before
    logger.info(
        f"[test] Credential '{test_case}' from '{issuer_name}' issued to wallet "
        f"(wallet: {count_before} → {count_after}, +{added})"
    )
    # The card count is the only evidence the credential landed; reaching the home screen only
    # proves the flow did not crash. The count was probed before this assertion was added
    # (2026-08-19): the locator matched exactly the cards present and did not grow when the list
    # was scrolled, so a flat count means nothing arrived, not that counting saturated.
    #
    # Tagged so the report can tell "accepted but not stored" from "never saw the offer".
    assert count_after > count_before, (
        f"[{outcome.NOT_STORED}] Credential '{test_case}' from '{issuer_name}' did not reach the "
        f"wallet: hovi accepted the offer but the card count stayed at {count_after}"
    )
