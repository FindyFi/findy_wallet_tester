import importlib
import logging
import pytest
from pathlib import Path

from base import outcome
from base.credential_count import CredentialCountUnavailable
from base.test_cases import verification_cases
from providers.factory import get_provider
from wallets.sphereon.pages.home_page import HomePage

logger = logging.getLogger(__name__)

APP_NAME = Path(__file__).parents[1].name
verification_flow = importlib.import_module(f"wallets.{APP_NAME}.flows.verification_flow")
_verification_cases = verification_cases(APP_NAME)


@pytest.mark.parametrize("driver", [APP_NAME], indirect=True)
@pytest.mark.parametrize("issuer_name,test_case", _verification_cases)
def test_credential_verification(app, issuer_name, test_case):
    pin = app.config["application"]["pin"]
    app_package = app.config["application"]["package"]

    # "Nothing was shared" has two causes, and they blame opposite parties: the verifier asked for
    # something this wallet does not hold, or issuance failed earlier in this run and there was
    # nothing to hold. Checking first keeps the second from being published as the first.
    #
    # sphereon needs a stronger check than "is the wallet empty": onboarding self-issues a
    # "Sphereon Wallet Identity" card, so a wallet nobody has managed to issue into still counts 1.
    home = HomePage(app.driver, **app.page_args)
    try:
        held = home.count_credentials()
    except CredentialCountUnavailable as e:
        logger.warning(f"[test] Could not count credentials before verification: {e}")
    else:
        own_only = held > 0 and home.holds_only_own_identity()
        if held == 0 or own_only:
            detail = ("holds nothing but the identity card onboarding issued to itself"
                      if own_only else "holds no credentials")
            pytest.fail(
                f"[{outcome.NOTHING_TO_PRESENT}] Cannot verify '{test_case}' against "
                f"'{issuer_name}': the wallet {detail}, so nothing an issuer sent could be "
                "shared. Issuance failed earlier in this run."
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
