"""unime's verdicts that the shared outcome code cannot reach on its own."""
from base import outcome
from wallets.unime.pages import error_page


def raise_for(state, driver, screens, ctx, *, expected, timeout, interstitials=()):
    """`outcome.raise_for`, except that unime's refusals worded as the current copy carry the
    `[no_retry]` tag: the same refusal came back on every attempt, so a rerun only costs time."""
    try:
        outcome.raise_for(state, driver, screens, ctx, expected=expected, timeout=timeout,
                          interstitials=interstitials)
    except outcome.FlowFailure as exc:
        if exc.category == outcome.REJECTED and error_page.keeps_no_retry(_said(exc)) \
                and "[no_retry]" not in str(exc):
            raise outcome.FlowFailure(outcome.REJECTED, f"{_body(exc)} [no_retry]") from None
        raise


def _body(exc) -> str:
    prefix = f"[{exc.category}] "
    text = str(exc)
    return text[len(prefix):] if text.startswith(prefix) else text


def _said(exc) -> str:
    """The quoted refusal inside the message, as `error_page.message` returned it."""
    start = str(exc).find('"')
    end = str(exc).find('". ', start + 1)
    return str(exc)[start + 1:end] if start >= 0 and end > start else ""
