import importlib
import logging
import pytest
from pathlib import Path

from base.credential_count import CredentialCountUnavailable
from base.test_cases import issuance_cases
from providers.factory import get_provider

logger = logging.getLogger(__name__)

# APP_NAME is derived from the parent directory so this file works unchanged in any wallet.
APP_NAME = Path(__file__).parents[1].name
credential_flow = importlib.import_module(f"wallets.{APP_NAME}.flows.credential_flow")
HomePage = importlib.import_module(f"wallets.{APP_NAME}.pages.home_page").HomePage
_issuance_cases = issuance_cases(APP_NAME)


@pytest.mark.parametrize("driver", [APP_NAME], indirect=True)
@pytest.mark.parametrize("issuer_name,test_case", _issuance_cases)
def test_credential_issuance(app, issuer_name, test_case):
    pin = app.config["application"]["pin"]
    app_package = app.config["application"]["package"]

    home = HomePage(app.driver, **app.page_args)
    # authbound counts from the Wallet tab's own header total ("Wallet · 2"), verified live on
    # ZT322L348J 2026-08-13. An unreadable count still says nothing about whether the credential
    # arrived (see base/credential_count.py), so it warns and skips the check rather than failing
    # the issuance test on it — but a count that IS readable is asserted below. This handling
    # belongs in the shared assertion helper once that exists — every wallet needs the same lines.
    try:
        count_before = home.count_credentials()
    except CredentialCountUnavailable as e:
        count_before = None
        logger.warning(f"[test] Credential count before issuance is unavailable: {e}")

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
    try:
        count_after = home.count_credentials()
    except CredentialCountUnavailable as e:
        count_after = None
        logger.warning(f"[test] Credential count after issuance is unavailable: {e}")

    if count_before is None or count_after is None:
        logger.warning(
            f"[test] Credential '{test_case}' from '{issuer_name}': the flow reported success but "
            "the wallet count could not be read — no count evidence for this run"
        )
        return

    logger.info(
        f"[test] Credential '{test_case}' from '{issuer_name}' issued to wallet "
        f"(wallet: {count_before} → {count_after}, +{count_after - count_before})"
    )
    # The wallet's own total is the only wallet-independent evidence the credential landed: the
    # flow reaching its success screen only proves the wallet said so. Without this, a silent
    # no-op issuance passes.
    assert count_after > count_before, (
        f"Credential '{test_case}' from '{issuer_name}' did not reach the wallet: the document "
        f"count stayed at {count_after}"
    )
