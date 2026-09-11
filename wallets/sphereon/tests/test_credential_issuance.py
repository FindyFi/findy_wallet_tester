import importlib
import logging
import pytest
from pathlib import Path

from base import credential_count
from base.test_cases import issuance_cases
from providers.factory import get_provider
from wallets.sphereon.pages.home_page import HomePage

logger = logging.getLogger(__name__)

APP_NAME = Path(__file__).parents[1].name
credential_flow = importlib.import_module(f"wallets.{APP_NAME}.flows.credential_flow")
_issuance_cases = issuance_cases(APP_NAME)


@pytest.mark.parametrize("driver", [APP_NAME], indirect=True)
@pytest.mark.parametrize("issuer_name,test_case", _issuance_cases)
def test_credential_issuance(app, request, issuer_name, test_case):
    pin = app.config["application"]["pin"]
    app_package = app.config["application"]["package"]

    home = HomePage(app.driver, **app.page_args)
    count_before = credential_count.read(home, when="before issuance")
    logger.info(f"[test] Credentials in wallet before testing '{issuer_name}': {count_before}")

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
    count_after = credential_count.read(home, when="after issuance")
    credential_count.assert_increased(
        count_before, count_after,
        request=request, wallet=APP_NAME, issuer=issuer_name, case=test_case,
    )
