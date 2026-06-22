"""End-of-run credential reset for Gataca.

This is forced to run LAST (see conftest.pytest_collection_modifyitems — @pytest.mark.gataca_cleanup
sorts after everything). After the issuance/verification matrix has filled the wallet, it switches
to each configured DID method and deletes every credential it can (the self-attested device
credential is always kept), so the wallet starts the next run clean.
"""
import importlib
import json
import logging
from pathlib import Path

import pytest

logger = logging.getLogger(__name__)

APP_NAME = Path(__file__).parents[1].name
setup_flow = importlib.import_module(f"wallets.{APP_NAME}.flows.setup_flow")
cleanup_flow = importlib.import_module(f"wallets.{APP_NAME}.flows.cleanup_flow")
init_flow = importlib.import_module(f"wallets.{APP_NAME}.flows.init_flow")
_config = json.loads((Path(__file__).parents[1] / "config.json").read_text())

from base.conftest_helpers import navigate_to_home


def _configured_methods():
    methods = _config.get("did_method", setup_flow.DEFAULT_DID_METHOD)
    return [methods] if isinstance(methods, str) else methods


@pytest.mark.gataca_cleanup
@pytest.mark.parametrize("driver", [APP_NAME], indirect=True)
def test_reset_credentials(app, request):
    app_package = app.config["application"]["package"]
    total = 0
    for method in _configured_methods():
        setup_flow.ensure_did(app.driver, app_package=app_package, did_method=method, **app.page_args)
        navigate_to_home(app, request, init_flow)
        deleted = cleanup_flow.prune_credentials(app.driver, max_count=0, **app.page_args)
        logger.info(f"[cleanup] DID '{method}': deleted {deleted} credential(s)")
        total += deleted
    logger.info(f"[cleanup] Reset complete — deleted {total} credential(s) across {_configured_methods()}")
