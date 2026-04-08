import logging
import pytest
from pathlib import Path

logger = logging.getLogger(__name__)

APP_NAME = Path(__file__).parents[1].name


@pytest.mark.parametrize("driver", [APP_NAME], indirect=True)
def test_app_launch(app):
    logger.info(f"App {app.config['application']['package']} is installed and launched successfully!")
