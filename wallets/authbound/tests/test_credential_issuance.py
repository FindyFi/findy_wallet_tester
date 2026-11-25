import importlib
import pytest
from pathlib import Path

from base import credential_count
from base.test_cases import issuance_cases
from providers.factory import get_provider

# APP_NAME is derived from the parent directory so this file works unchanged in any wallet.
APP_NAME = Path(__file__).parents[1].name
credential_flow = importlib.import_module(f"wallets.{APP_NAME}.flows.credential_flow")
HomePage = importlib.import_module(f"wallets.{APP_NAME}.pages.home_page").HomePage
_issuance_cases = issuance_cases(APP_NAME)


@pytest.mark.parametrize("driver", [APP_NAME], indirect=True)
@pytest.mark.parametrize("issuer_name,test_case", _issuance_cases)
def test_credential_issuance(app, request, issuer_name, test_case):
    pin = app.config["application"]["pin"]
    app_package = app.config["application"]["package"]

    home = HomePage(app.driver, **app.page_args)
    # authbound counts from the Wallet tab's own header total ("Wallet · 2"), verified live on
    # ZT322L348J 2026-08-13. An unreadable count says nothing about whether the credential arrived,
    # so the shared helpers warn and record the missing evidence rather than failing on it — the
    # same handling this test used to spell out itself.
    count_before = credential_count.read(home, when="before issuance")

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

    # The wallet's own total is the only wallet-independent evidence the credential landed: the
    # flow reaching its success screen only proves the wallet said so. Without this, a silent
    # no-op issuance passes. A flat count fails as `not_stored`, with the category attached.
    credential_count.assert_increased(count_before, count_after, request=request,
                                      wallet=APP_NAME, issuer=issuer_name, case=test_case)
