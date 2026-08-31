import importlib
import logging
import pytest
from pathlib import Path

from base.test_cases import verification_cases
from providers.factory import get_provider

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
