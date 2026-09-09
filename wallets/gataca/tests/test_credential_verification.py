import importlib
import logging
import pytest
from pathlib import Path

from base.config import as_list, wallet_config
from base.test_cases import verification_cases
from providers.factory import get_provider
from wallets.gataca.pages.home_page import HomePage

logger = logging.getLogger(__name__)

APP_NAME = Path(__file__).parents[1].name
verification_flow = importlib.import_module(f"wallets.{APP_NAME}.flows.verification_flow")
setup_flow = importlib.import_module(f"wallets.{APP_NAME}.flows.setup_flow")
_verification_cases = verification_cases(APP_NAME)

# DID method(s) to run each case under (see test_credential_issuance for the rationale).
# Chosen with GATACA_DID_METHODS in .env.
_did_methods = as_list(wallet_config(APP_NAME).get("did_method", setup_flow.DEFAULT_DID_METHOD))


@pytest.mark.gataca_did
@pytest.mark.parametrize("did_method", _did_methods)
@pytest.mark.parametrize("driver", [APP_NAME], indirect=True)
@pytest.mark.parametrize("issuer_name,test_case", _verification_cases)
def test_credential_verification(app, issuer_name, test_case, did_method):
    logger.info(f"[test] Verification '{test_case}' from '{issuer_name}' under DID method '{did_method}'")
    pin = app.config["application"]["pin"]
    app_package = app.config["application"]["package"]

    # Verify the wallet is on the DID this test is meant to run under (it reverts to the default on
    # the app's cold start, so a failed re-switch must not silently pass on the wrong DID).
    expected_alias = setup_flow.DID_METHODS[did_method]["alias"]
    active_alias = HomePage(app.driver, **app.page_args).active_did_alias()
    assert active_alias == expected_alias, (
        f"Active DID is '{active_alias}', expected '{expected_alias}' for did_method '{did_method}'"
    )

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
