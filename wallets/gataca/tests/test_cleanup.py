"""End-of-run credential reset for Gataca.

This is forced to run LAST (see conftest.pytest_collection_modifyitems — @pytest.mark.gataca_cleanup
sorts after everything). After the issuance/verification matrix has filled the wallet, it switches
to each configured DID method and deletes every credential it can (the self-attested device
credential is always kept), so the wallet starts the next run clean.
"""
import importlib
import logging
from pathlib import Path

import pytest

logger = logging.getLogger(__name__)

APP_NAME = Path(__file__).parents[1].name
setup_flow = importlib.import_module(f"wallets.{APP_NAME}.flows.setup_flow")
cleanup_flow = importlib.import_module(f"wallets.{APP_NAME}.flows.cleanup_flow")
init_flow = importlib.import_module(f"wallets.{APP_NAME}.flows.init_flow")
from base.config import as_list, wallet_config
from base.conftest_helpers import navigate_to_home


def _configured_methods():
    """Every DID method this run exercised, so cleanup empties the wallet under each of them."""
    return as_list(wallet_config(APP_NAME).get("did_method", setup_flow.DEFAULT_DID_METHOD))


@pytest.mark.gataca_cleanup
@pytest.mark.parametrize("driver", [APP_NAME], indirect=True)
def test_reset_credentials(app, request):
    app_package = app.config["application"]["package"]
    # From app.config, not the raw config.json: settings are expanded from the environment at load
    # time, so reading the file directly yields the literal placeholder instead of a number.
    keep = app.config.get("cleanup", {}).get("max_credentials", 0)
    total = 0
    for method in _configured_methods():
        setup_flow.ensure_did(app.driver, app_package=app_package, did_method=method, **app.page_args)
        navigate_to_home(app, request, init_flow)
        deleted = cleanup_flow.prune_credentials(app.driver, max_count=keep, **app.page_args)
        logger.info(f"[cleanup] DID '{method}': deleted {deleted} credential(s)")
        total += deleted
    logger.info(f"[cleanup] Reset complete — deleted {total} credential(s) across {_configured_methods()}")
