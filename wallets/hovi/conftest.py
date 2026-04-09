import logging

import pytest

from base.conftest_helpers import navigate_to_home, teardown_test
from wallets.hovi.flows import init_flow

logger = logging.getLogger(__name__)


@pytest.fixture(autouse=True)
def _ensure_home(app, request):
    """Ensure the wallet is on the home screen before and after every test.

    Tag a test with @pytest.mark.skip_home_setup to opt out entirely:
        @pytest.mark.skip_home_setup
        def test_something_special(app): ...
    """
    if request.node.get_closest_marker("skip_home_setup"):
        yield
        return

    navigate_to_home(app, request, init_flow)
    yield
    teardown_test(app, request, init_flow)
