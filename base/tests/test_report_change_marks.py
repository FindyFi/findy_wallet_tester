"""annotate_changes: the "What moved" marks against the previous run.

Cells kept out of the pass and fail totals (a lost session, a framework failure) get no
"Newly failing" / "Newly passing" mark, on either side of the comparison.
"""
import pytest

from generate_compact_report import annotate_changes


def _run(outcome, reason=None, was="Passed"):
    """One cell now and its previous-run token; returns the cell after annotation."""
    cell = {"outcome": outcome, "reason": reason}
    matrix = {"run_ts": "now", "issuance": {"wallet": {"issuer": cell}}}
    previous = {"run_ts": "before", "cells": {"issuance": {"wallet": {"issuer": was}}}}
    annotate_changes(matrix, previous)
    return cell, matrix["change_counts"]


@pytest.mark.parametrize("was, outcome, reason, change", [
    # Counted on both sides: the marks work as before.
    ("Passed", "Failed", "rejected", "broke"),
    ("Failed:rejected", "Passed", None, "fixed"),
    ("Failed:rejected", "Failed", "absent", "reason"),
    ("Passed", "Error", "crashed", "broke"),
    # Not counted now: a lost session after a pass did not newly fail.
    ("Passed", "Failed", "lost_session", None),
    ("Passed", "Error", "lost_session", None),
    ("Passed", "Error", "harness", None),
    # Not counted last time: a pass after a lost session did not newly pass.
    ("Failed:lost_session", "Passed", None, None),
    ("Error:harness", "Passed", None, None),
    # Red both times keeps its "different reason" mark, counted or not.
    ("Failed:rejected", "Failed", "lost_session", "reason"),
    ("Failed:lost_session", "Failed", "rejected", "reason"),
    # Same reading as last time: unmarked.
    ("Failed:lost_session", "Failed", "lost_session", None),
])
def test_change_mark(was, outcome, reason, change):
    cell, counts = _run(outcome, reason, was)
    assert cell.get("change") == change
    assert counts == ({change: 1} if change else {})
