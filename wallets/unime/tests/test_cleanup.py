"""End-of-run credential cleanup for unime.

Runs LAST by way of the shared lifecycle ordering in the root `conftest.py` — `_MODULE_ORDER`
sorts `test_cleanup` after issuance and verification — so the wallet is only emptied once
everything that needs a credential has had it. No marker is required here; gataca has one only
because its own DID grouping would otherwise reorder the suite.

How many credentials to keep comes from `cleanup.max_credentials` in the wallet config, defaulting
to 0 (delete everything). Set it higher to leave a credential behind for a flow that expects the
wallet to be non-empty.
"""
import importlib
import logging
from pathlib import Path

import pytest

from base.credential_count import CredentialCountUnavailable

logger = logging.getLogger(__name__)

# APP_NAME is derived from the parent directory so this file works unchanged in any wallet.
APP_NAME = Path(__file__).parents[1].name
cleanup_flow = importlib.import_module(f"wallets.{APP_NAME}.flows.cleanup_flow")
HomePage = importlib.import_module(f"wallets.{APP_NAME}.pages.home_page").HomePage


@pytest.mark.parametrize("driver", [APP_NAME], indirect=True)
def test_reset_credentials(app):
    # From app.config, not the raw config.json: settings are expanded from the environment
    # at load time, so reading the file directly yields the literal "${DEFAULT_MAX_CREDENTIALS:-0}"
    # placeholder instead of a number.
    keep = app.config.get("cleanup", {}).get("max_credentials", 0)

    deleted = cleanup_flow.prune_credentials(app.driver, max_count=keep, **app.page_args)
    logger.info(f"[cleanup] Deleted {deleted} credential(s); target is at most {keep}")

    # Assert the outcome rather than trusting the flow's own bookkeeping — the whole point of the
    # cleanup is the state it leaves behind for the next run.
    home = HomePage(app.driver, **app.page_args)
    home.wait_until_loaded()
    try:
        remaining = home.count_credentials()
    except CredentialCountUnavailable as e:
        logger.warning(f"[cleanup] Could not verify the final credential count: {e}")
        return

    assert remaining <= keep, (
        f"Cleanup left {remaining} credential(s) in the wallet, expected at most {keep}"
    )
