import importlib
import logging
import pytest
from pathlib import Path

from base.config import as_list, wallet_config
from base.test_cases import issuance_cases
from providers.factory import get_provider
from wallets.gataca.pages.home_page import HomePage

logger = logging.getLogger(__name__)

APP_NAME = Path(__file__).parents[1].name
credential_flow = importlib.import_module(f"wallets.{APP_NAME}.flows.credential_flow")
setup_flow = importlib.import_module(f"wallets.{APP_NAME}.flows.setup_flow")
_issuance_cases = issuance_cases(APP_NAME)

# DID method(s) to run each case under: one ("jwk") or several ("jwk,ebsi") to run the matrix
# across methods. The conftest switches the active DID per method group, and the report shows
# pass/fail per (method × issuer). Set GATACA_DID_METHODS in .env to choose; the committed
# default is in wallets/gataca/config.json.
_did_methods = as_list(wallet_config(APP_NAME).get("did_method", setup_flow.DEFAULT_DID_METHOD))
_unknown = [m for m in _did_methods if m not in setup_flow.DID_METHODS]
if _unknown:
    # Loudly, not by quietly dropping the row: results are published, and a matrix missing a DID
    # method with nothing to say why is worse than a run that refuses to start.
    raise pytest.UsageError(
        f"GATACA_DID_METHODS names {'a DID method' if len(_unknown) == 1 else 'DID methods'} the "
        f"gataca wallet does not support: {', '.join(sorted(_unknown))}.\n"
        f"  Known methods: {', '.join(setup_flow.DID_METHODS)}"
    )


@pytest.mark.gataca_did
@pytest.mark.parametrize("did_method", _did_methods)
@pytest.mark.parametrize("driver", [APP_NAME], indirect=True)
@pytest.mark.parametrize("issuer_name,test_case", _issuance_cases)
def test_credential_issuance(app, issuer_name, test_case, did_method):
    logger.info(f"[test] Issuance '{test_case}' from '{issuer_name}' under DID method '{did_method}'")
    pin = app.config["application"]["pin"]
    app_package = app.config["application"]["package"]

    home = HomePage(app.driver, **app.page_args)

    # Verify the wallet is actually on the DID this test is meant to run under (it reverts to the
    # default on the app's cold start, so a failed re-switch must not silently pass on the wrong DID).
    expected_alias = setup_flow.DID_METHODS[did_method]["alias"]
    active_alias = home.active_did_alias()
    assert active_alias == expected_alias, (
        f"Active DID is '{active_alias}', expected '{expected_alias}' for did_method '{did_method}'"
    )

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
    assert added > 0, (
        f"Wallet reported the issuance flow as successful but credential count did not "
        f"increase ({count_before} → {count_after}). The Gataca wallet may have completed "
        f"OIDC authentication ('Login Successful') without actually storing the issued "
        f"credential — check the wallet UI and notifications."
    )
