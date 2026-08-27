import importlib
import json
import logging
import pytest
from pathlib import Path

from base.credential_count import CredentialCountUnavailable
from providers.factory import get_provider
from wallets.hovi.flows import outcome
from wallets.hovi.pages.home_page import HomePage

logger = logging.getLogger(__name__)

# APP_NAME is derived from the parent directory so this file works unchanged in any wallet.
APP_NAME = Path(__file__).parents[1].name
verification_flow = importlib.import_module(f"wallets.{APP_NAME}.flows.verification_flow")
_config = json.loads(
    (Path(__file__).parents[1] / "config.json").read_text()
)
_verification_cases = [
    pytest.param(issuer_name, cred_name, id=f"{issuer_name}/{cred_name}")
    for issuer_name, issuer_cfg in _config.get("test_cases", {}).items()
    for cred_name, cred_cfg in issuer_cfg.get("credentials", {}).items()
    if cred_cfg.get("type") == "verification"
]


@pytest.mark.parametrize("driver", [APP_NAME], indirect=True)
@pytest.mark.parametrize("issuer_name,test_case", _verification_cases or [pytest.param(
    "", "", marks=pytest.mark.skip(reason="No verification test cases configured in config")
)])
def test_credential_verification(app, issuer_name, test_case):
    pin = app.config["application"]["pin"]
    app_package = app.config["application"]["package"]

    # Runs start from a wiped wallet, so "nothing was shared" has two causes: verifier and wallet
    # could not agree, or the wallet was empty because issuance failed earlier in this run.
    # Checking first keeps the second from reading as the verifier's fault.
    try:
        held = HomePage(app.driver, **app.page_args).count_credentials()
    except CredentialCountUnavailable as e:
        logger.warning(f"[test] Could not count credentials before verification: {e}")
    else:
        if held == 0:
            pytest.fail(
                f"[{outcome.NOTHING_TO_PRESENT}] Cannot verify '{test_case}' against "
                f"'{issuer_name}': the wallet holds no credentials, so nothing could be shared. "
                "Issuance failed earlier in this run."
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
