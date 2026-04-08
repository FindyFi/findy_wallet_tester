import importlib
import logging
import pytest
from pathlib import Path

logger = logging.getLogger(__name__)

# APP_NAME is derived from the parent directory so this file works unchanged in any wallet.
APP_NAME = Path(__file__).parents[1].name
init_flow = importlib.import_module(f"wallets.{APP_NAME}.flows.init_flow")
HomePage = importlib.import_module(f"wallets.{APP_NAME}.pages.home_page").HomePage


@pytest.mark.parametrize("driver", [APP_NAME], indirect=True)
def test_onboarding(app):
    pin = app.config["application"]["pin"]
    skip_if_done = app.config.get("onboarding", {}).get("skip_if_done", True)
    app_package = app.config["application"]["package"]
    init_flow.run(app.driver, pin=pin, skip_if_done=skip_if_done, app_package=app_package, **app.page_args)
    HomePage(app.driver, **app.page_args).wait_until_loaded()
    logger.info("Onboarding completed — wallet home screen reached")
