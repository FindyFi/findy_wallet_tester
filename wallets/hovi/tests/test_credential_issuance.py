import importlib
import logging
import pytest
from pathlib import Path

from base.test_cases import issuance_cases
from providers.factory import get_provider
from base import credential_count
from wallets.hovi.pages.home_page import HomePage

logger = logging.getLogger(__name__)

# APP_NAME is derived from the parent directory so this file works unchanged in any wallet.
APP_NAME = Path(__file__).parents[1].name
credential_flow = importlib.import_module(f"wallets.{APP_NAME}.flows.credential_flow")
_issuance_cases = issuance_cases(APP_NAME)


@pytest.mark.parametrize("driver", [APP_NAME], indirect=True)
@pytest.mark.parametrize("issuer_name,test_case", _issuance_cases)
def test_credential_issuance(app, request, issuer_name, test_case):
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
    # The card count is the only evidence the credential landed; reaching the home screen only
    # proves the flow did not crash. The count was probed before this assertion was added
    # (2026-08-19): the locator matched exactly the cards present and did not grow when the list
    # was scrolled, so a flat count means nothing arrived, not that counting saturated.
    #
    # Through the shared check so the failure carries `not_stored` as a category, not only as text.
    # Both counts come from `count_credentials()` directly, as before: an unreadable count still
    # fails the test here rather than becoming `credential_count.read`'s warning.
    credential_count.assert_increased(count_before, count_after, request=request,
                                      wallet=APP_NAME, issuer=issuer_name, case=test_case)
