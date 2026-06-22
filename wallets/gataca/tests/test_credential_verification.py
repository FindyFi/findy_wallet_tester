import importlib
import json
import logging
import pytest
from pathlib import Path

from providers.factory import get_provider
from wallets.gataca.pages.home_page import HomePage

logger = logging.getLogger(__name__)

APP_NAME = Path(__file__).parents[1].name
verification_flow = importlib.import_module(f"wallets.{APP_NAME}.flows.verification_flow")
setup_flow = importlib.import_module(f"wallets.{APP_NAME}.flows.setup_flow")
_config = json.loads(
    (Path(__file__).parents[1] / "config.json").read_text()
)
_verification_cases = [
    pytest.param(
        issuer_name, cred_name,
        id=f"{issuer_name}/{cred_name}",
        marks=[pytest.mark.xfail(reason=cred_cfg["xfail"], strict=False)]
        if cred_cfg.get("xfail") else [],
    )
    for issuer_name, issuer_cfg in _config.get("test_cases", {}).items()
    for cred_name, cred_cfg in issuer_cfg.get("credentials", {}).items()
    if cred_cfg.get("type") == "verification"
]

# DID method(s) to run each case under (see test_credential_issuance for the rationale).
_did_methods = _config.get("did_method", "jwk")
if isinstance(_did_methods, str):
    _did_methods = [_did_methods]


@pytest.mark.gataca_did
@pytest.mark.parametrize("did_method", _did_methods)
@pytest.mark.parametrize("driver", [APP_NAME], indirect=True)
@pytest.mark.parametrize("issuer_name,test_case", _verification_cases or [pytest.param(
    "", "", marks=pytest.mark.skip(reason="No verification test cases configured in config")
)])
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
