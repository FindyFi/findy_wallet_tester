import importlib
import json
import logging
import pytest
from pathlib import Path

from providers.factory import get_provider
from wallets.gataca.pages.home_page import HomePage

logger = logging.getLogger(__name__)

APP_NAME = Path(__file__).parents[1].name
credential_flow = importlib.import_module(f"wallets.{APP_NAME}.flows.credential_flow")
setup_flow = importlib.import_module(f"wallets.{APP_NAME}.flows.setup_flow")
_config = json.loads(
    (Path(__file__).parents[1] / "config.json").read_text()
)
_issuance_cases = [
    pytest.param(
        issuer_name, cred_name,
        id=f"{issuer_name}/{cred_name}",
        marks=[pytest.mark.xfail(reason=cred_cfg["xfail"], strict=False)]
        if cred_cfg.get("xfail") else [],
    )
    for issuer_name, issuer_cfg in _config.get("test_cases", {}).items()
    for cred_name, cred_cfg in issuer_cfg.get("credentials", {}).items()
    if cred_cfg.get("type") == "issuance"
]

# DID method(s) to run each case under. Config "did_method" is a single value ("jwk") or a list
# (["jwk","gatc","ebsi"]) to run the matrix across methods; the conftest switches the active DID
# per method group and the report shows pass/fail per (method × issuer).
_did_methods = _config.get("did_method", "jwk")
if isinstance(_did_methods, str):
    _did_methods = [_did_methods]


@pytest.mark.gataca_did
@pytest.mark.parametrize("did_method", _did_methods)
@pytest.mark.parametrize("driver", [APP_NAME], indirect=True)
@pytest.mark.parametrize("issuer_name,test_case", _issuance_cases or [pytest.param(
    "", "", marks=pytest.mark.skip(reason="No issuance test cases configured in config")
)])
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
