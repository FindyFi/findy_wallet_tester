"""A flat credential count, run as a child session by test_report_reasons.py.

Not named `test_*.py`, so the real suite never collects it: it fails on purpose. It sits in the
repo so the child session loads the real root `conftest.py`, whose makereport hook is what reads
`.category` off the exception.
"""
import json
import os
from types import SimpleNamespace

import pytest

from base import credential_count


@pytest.fixture
def report_what_the_hook_left(request):
    yield
    (open(os.environ["HOOKCHECK_OUT"], "w")).write(json.dumps({
        "line": getattr(request.node, "_failure_line", None),
        "category": getattr(request.node, "_failure_category", None),
    }))


def test_a_flat_count(report_what_the_hook_left):
    credential_count.assert_increased(
        3, 3, request=SimpleNamespace(node=SimpleNamespace(user_properties=[])),
        wallet="hovi", issuer="hovi_issuer", case="credential_issuance")
