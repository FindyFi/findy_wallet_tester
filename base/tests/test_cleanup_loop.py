"""The credential-prune loop, exercised with fake gestures and no device.

The loop is now shared by every wallet that can delete, so a mistake in it is a mistake in all of
them at once — and its failure modes are the expensive kind to find on a phone. A prune that spins
forever burns a whole run before anyone sees it; a prune that deletes from a wallet whose count is
unreadable destroys the evidence the run was collecting. Both bounds are pinned here.

Fake gestures also let the *decision* be tested apart from the gestures, which is the whole point
of the split: these cases never touch a locator.

Like test_outcome_state_machine.py, these need no Appium session.
"""
import pytest

from base import cleanup
from base.cleanup import DeleteRefused
from base.credential_count import CredentialCountUnavailable


class FakeDriver:
    current_package = "com.example.wallet"
    capabilities: dict = {}


class FakeHome:
    """A wallet whose count falls by one on every delete, unless told otherwise."""

    def __init__(self, count, unavailable_after=None):
        self.count = count
        self.unavailable_after = unavailable_after
        self.reads = 0
        self.loads = 0

    def wait_until_loaded(self):
        self.loads += 1

    def count_credentials(self):
        self.reads += 1
        if self.unavailable_after is not None and self.reads > self.unavailable_after:
            raise CredentialCountUnavailable("fake wallet lost its count label")
        return self.count


def prune(home, **kw):
    kw.setdefault("wallet", "testwallet")
    kw.setdefault("open_detail", lambda: True)
    return cleanup.prune_credentials(FakeDriver(), home=home, **kw)


# --- the ordinary case ----------------------------------------------------------------------

def test_deletes_down_to_the_target():
    home = FakeHome(3)

    def delete():
        home.count -= 1

    assert prune(home, delete=delete, max_count=1) == 2
    assert home.count == 1


def test_a_wallet_already_at_the_target_is_left_alone():
    home = FakeHome(0)
    deleted = prune(home, delete=lambda: pytest.fail("deleted from an empty wallet"),
                    max_count=0)
    assert deleted == 0


def test_target_is_at_most_not_exactly():
    """A count below the target is not topped up — `max_count` is a ceiling, not a quota."""
    home = FakeHome(1)
    assert prune(home, delete=lambda: pytest.fail("deleted below the target"), max_count=5) == 0


# --- the two bounds, which are why this loop is worth sharing ---------------------------------

def test_an_unreadable_count_stops_the_prune_rather_than_deleting_blind():
    """No count means no way to know when to stop, so nothing is deleted.

    Leaving a wallet dirty is recoverable; deleting from a wallet whose contents we cannot see is
    not, and it destroys the very state the run was measuring.
    """
    home = FakeHome(3, unavailable_after=0)
    assert prune(home, delete=lambda: pytest.fail("deleted with no count"), max_count=0) == 0


def test_an_unreadable_count_partway_through_stops_where_it_is():
    home = FakeHome(5, unavailable_after=2)

    def delete():
        home.count -= 1

    assert prune(home, delete=delete, max_count=0) == 2


def test_a_delete_that_does_not_lower_the_count_hits_the_cap_instead_of_spinning():
    """The infinite-loop case: the wallet reports success and keeps the credential.

    Without the cap the count never falls, so the loop never ends — a whole run lost to one
    misbehaving delete.
    """
    home = FakeHome(3)
    calls = {"n": 0}

    def delete():
        calls["n"] += 1

    assert prune(home, delete=delete, max_count=0, max_deletions=7) == 7
    assert calls["n"] == 7


# --- ways a wallet says "not this one" --------------------------------------------------------

def test_no_openable_card_is_a_normal_end_not_a_failure():
    """gataca's standing case: credentials it will not let anyone delete.

    The count stays above the target and that is correct, so this must not raise.
    """
    home = FakeHome(1)
    assert prune(home, open_detail=lambda: False,
                 delete=lambda: pytest.fail("deleted an unopenable card"), max_count=0) == 0
    assert home.count == 1


def test_can_delete_false_stops_and_closes_the_detail():
    home = FakeHome(2)
    closed = {"n": 0}
    assert prune(home, can_delete=lambda: False, close_detail=lambda: closed.__setitem__("n", 1),
                 delete=lambda: pytest.fail("deleted a protected credential"), max_count=0) == 0
    assert closed["n"] == 1


def test_can_delete_false_without_a_close_detail_still_stops():
    """`close_detail` is optional, so its absence must not turn a clean stop into a TypeError."""
    home = FakeHome(2)
    assert prune(home, can_delete=lambda: False,
                 delete=lambda: pytest.fail("deleted a protected credential"), max_count=0) == 0


# --- refusal, and the class identity that made it silently dead -------------------------------

def test_a_refusal_with_no_recovery_is_raised():
    """A wallet that says yes and does nothing is a real failure when nobody can recover it."""
    home = FakeHome(2)

    def delete():
        raise DeleteRefused("wallet kept the credential")

    with pytest.raises(DeleteRefused):
        prune(home, delete=delete, max_count=0)


def test_on_refused_returning_true_retries():
    home = FakeHome(2)
    attempts = {"n": 0}

    def delete():
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise DeleteRefused("first attempt refused")
        home.count -= 1

    assert prune(home, delete=delete, on_refused=lambda: True, max_count=1) == 1
    assert attempts["n"] == 2


def test_on_refused_returning_false_raises_so_a_spent_budget_is_still_a_failure():
    """hovi's restart budget runs out this way, and the prune must fail rather than quietly stop.

    A silent stop here would publish a green cleanup for a wallet that still holds everything.
    """
    home = FakeHome(2)

    def delete():
        raise DeleteRefused("refused every time")

    with pytest.raises(DeleteRefused):
        prune(home, delete=delete, on_refused=lambda: False, max_count=0)


def test_the_refusal_type_is_the_one_base_catches():
    """Pins the bug that made hovi's retry dead code.

    hovi defined its own `DeleteRefused(RuntimeError)` with the same name, so the exception it
    raised was never the class this loop catches — and the retry it exists for would have gone
    unused, on exactly the intermittent failure nobody watches closely.
    """
    from wallets.hovi.pages import credential_detail_page as hovi_detail
    assert hovi_detail.DeleteRefused is cleanup.DeleteRefused
