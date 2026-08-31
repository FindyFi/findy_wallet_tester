import importlib
import logging
import pytest
from pathlib import Path

from base.test_cases import issuance_cases
from providers.factory import get_provider
from wallets.toppan.pages.home_page import HomePage

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
    assert count_after > count_before, (
        f"Credential was not added to wallet (count before: {count_before}, after: {count_after})"
    )
    logger.info(f"[test] Credential added — wallet now has {count_after} credential(s)")
