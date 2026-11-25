import logging

import pytest

from base.conftest_helpers import navigate_to_home, teardown_test
from wallets.paradym.flows import init_flow, settings_flow

logger = logging.getLogger(__name__)


@pytest.fixture(autouse=True)
def _ensure_home(app, request):
    """Ensure the wallet is on the home screen before and after every test.

    The first test of a session also turns paradym's Development Mode on (settings_flow): a fresh
    install has it off, and then its error screens lack the "Development mode error" detail that
    the retry rules in flows/_failures.py read. That detour ends on home; when it does not,
    init_flow finds its way back.

    Tag a test with @pytest.mark.skip_home_setup to opt out entirely:
        @pytest.mark.skip_home_setup
        def test_something_special(app): ...
    """
    if request.node.get_closest_marker("skip_home_setup"):
        yield
        return

    navigate_to_home(app, request, init_flow)
    if not settings_flow.ensure_dev_mode_once(app.driver, request.config, **app.page_args):
        navigate_to_home(app, request, init_flow)
    yield
    teardown_test(app, request, init_flow)
