"""How far a flow got before it failed, recorded as it goes rather than inferred afterwards.

An outcome category says *why* a flow stopped; it cannot say *where*. Twenty-four of the forty-one
red cells in the 2026-09-21 run read "rejected", and they span paradym failing straight after the
deeplink and paradym failing after the PIN — one word for flows that got nowhere and flows that
got nearly to the end. The report used to place every cell on the flow diagram by its category
alone (`rejected` always drawn at "accept"), which is an inference, not a measurement.

So a flow says when it *completes* a step, and the failure record carries the furthest one.

Two levels:

  * a **stage** is shared and comparable across wallets — the report's own flow diagram:
    deliver, open, present, match, accept, keep. `generate_compact_report.py` reads its stage keys
    from here, so the diagram and the record cannot drift apart.
  * a **step** is free text, per wallet: "consent confirmed", "answered device-pin (2)". It is
    what tells "first hurdle" from "nine steps in", and it is shown, never compared.

Module-level, per attempt, rather than on `FlowContext`: the root conftest has to read it for
*any* exception — a bare `Element … not found` from `base_page` carries no context — and most
wallets do not build a `FlowContext` at all. One pytest process runs one test at a time (xdist
workers are separate processes), so one record is enough. The root conftest resets it at the start
of every attempt.

**Only ever record what completed.** A missing call makes the trail coarser — the reported stage is
earlier than the truth — and never later. Nothing here raises: a diagnostic that breaks the flow it
describes is worse than no diagnostic.
"""
import logging
from typing import List, Tuple

logger = logging.getLogger(__name__)

DELIVER = "deliver"    # the URL was handed to Android and it routed it to the wallet
OPEN = "open"          # the wallet is in front, and acting on the link
PRESENT = "present"    # it showed the offer or the request
MATCH = "match"        # it holds something that satisfies the request (verification only)
ACCEPT = "accept"      # it took the credential, or handed the presentation over, without an error
KEEP = "keep"          # the credential is in the wallet

STAGES = (DELIVER, OPEN, PRESENT, MATCH, ACCEPT, KEEP)

# (stage, step) in the order they happened. `stage` is the furthest reached when the step was
# recorded, so a step that does not advance the flow is still placed on it.
_trail: List[Tuple[str, str]] = []


def reset() -> None:
    """Start a fresh record. Called by the root conftest before every attempt, retries included."""
    _trail.clear()


def _furthest() -> str:
    reached = [s for s, _ in _trail if s in STAGES]
    return max(reached, key=STAGES.index) if reached else ""


def reached(stage: str, step: str = "") -> None:
    """The flow completed `stage`. `step` says what, in the wallet's own terms."""
    try:
        if stage not in STAGES:
            logger.warning(f"[progress] Ignoring unknown stage {stage!r} ({step or 'no step'})")
            return
        _trail.append((stage, step))
        logger.info(f"[progress] reached {stage}" + (f": {step}" if step else ""))
    except Exception:
        pass


def step(text: str) -> None:
    """A step that happened without completing a stage — answered a prompt, tapped a button."""
    try:
        _trail.append((_furthest(), text))
        logger.info(f"[progress] {text}")
    except Exception:
        pass


def snapshot() -> dict:
    """The furthest stage completed and every step, in order. Empty when nothing was recorded."""
    try:
        return {
            "stage": _furthest(),
            "steps": [f"{s or 'start'}: {t}" if t else s for s, t in _trail],
        }
    except Exception:
        return {"stage": "", "steps": []}
