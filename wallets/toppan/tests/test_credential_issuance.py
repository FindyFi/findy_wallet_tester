import importlib
import json
import logging
import pytest
from pathlib import Path

from providers.factory import get_provider

logger = logging.getLogger(__name__)

# APP_NAME is derived from the parent directory so this file works unchanged in any wallet.
APP_NAME = Path(__file__).parents[1].name
credential_flow = importlib.import_module(f"wallets.{APP_NAME}.flows.credential_flow")
_config = json.loads(
    (Path(__file__).parents[1] / "config.json").read_text()
)
_issuance_cases = [
    pytest.param(issuer_name, cred_name, id=f"{issuer_name}/{cred_name}")
    for issuer_name, issuer_cfg in _config.get("test_cases", {}).items()
    for cred_name, cred_cfg in issuer_cfg.get("credentials", {}).items()
    if cred_cfg.get("type") == "issuance"
]


@pytest.mark.parametrize("driver", [APP_NAME], indirect=True)
@pytest.mark.parametrize("issuer_name,test_case", _issuance_cases or [pytest.param(
    "", "", marks=pytest.mark.skip(reason="No issuance test cases configured in config")
)])
def test_credential_issuance(app, issuer_name, test_case):
    pin = app.config["application"]["pin"]
    app_package = app.config["application"]["package"]

    provider = get_provider(app.config, issuer_name)
    credential_flow.run(
        app.driver,
        provider=provider,
        credential_name=test_case,
        app_package=app_package,
        pin=pin,
        **app.page_args,
    )
    logger.info(f"[test] Credential '{test_case}' from '{issuer_name}' issued to wallet")
