import logging

from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, WebDriverException

from base import deeplink, outcome, progress
from base.flow_context import FlowContext
from base.outcome import FlowFailure
from wallets.procivis.flows import _failures as failures
from wallets.procivis.pages.credential_offer_page import CredentialOfferPage, SCREEN_ID as _offer_id
from wallets.procivis.pages.credential_accept_process_page import (
    CredentialAcceptProcessPage,
    FAILED,
    SCREEN_ID as _process_id,
    present as _failure_on_screen,
    screen_name as _process_screen_name,
)
from wallets.procivis.pages.home_page import on_screen as _home_on_screen
from wallets.procivis.screens import SCREENS
from base.utils import wait_present
from providers.base import DeeplinkProvider

logger = logging.getLogger(__name__)


def _no_offer(driver, ctx: FlowContext, t: float, *, after: str):
    """The credential offer is not up when it should be. Returns which screen is up after all —
    "offer" or "process" — when procivis was only late; otherwise raises the honest diagnosis.

    Everything procivis could be showing is probed here, failure animation included, so the
    shared diagnosis's "no error" and "no home screen" are both true when it is reached.
    """
    try:
        if wait_present(driver, _offer_id, timeout=1):
            progress.step("procivis showed the credential offer late")
            return "offer"
        if _failure_on_screen(driver, timeout=1):
            progress.reached(progress.OPEN, "procivis was in front, showing its failure screen")
            failures.rejected(driver, ctx, head=(
                f"[{ctx.flow}] Credential issuance failed for '{ctx.what}'"))
        if wait_present(driver, _process_id, timeout=1):
            progress.step("procivis showed its process screen late")
            return "process"
        on_home = _home_on_screen(driver, timeout=1)
    except WebDriverException as exc:
        raise failures.lost(ctx, f"after {after}", exc) from exc
    if on_home:
        # One look, at the end: what was there then, not how long it had been there.
        progress.reached(progress.OPEN, "procivis was in front, on its wallet screen")
        raise FlowFailure(outcome.DISMISSED, (
            f"[{ctx.flow}] procivis showed its wallet screen, not a credential offer, when checked "
            f"{t:.0f}s after {after} for '{ctx.what}' — the deeplink was not processed or the "
            "issuer was rejected silently"
        ))
    outcome.raise_for(outcome.ABSENT, driver, SCREENS, ctx, expected="credential offer", timeout=t)


def _await_process(driver, ctx: FlowContext, *, when: str) -> None:
    """Wait on procivis's process screen. Returns once it closed cleanly; raises otherwise.

    Its failure animation is `rejected` (flows/_failures.py reads the label and the details); a
    screen that never leaves is `processing` (the page says so); a screen that closed under a crash
    dialog is `crashed`. A screen closing is still not procivis saying it stored anything: the
    test's count is what completes `keep`.
    """
    try:
        result = CredentialAcceptProcessPage(driver, **ctx.page_args).wait_for_result()
    except WebDriverException as exc:
        raise failures.lost(ctx, "on its process screen", exc) from exc
    if result == FAILED:
        failures.rejected(driver, ctx, head=(
            f"[{ctx.flow}] Credential issuance failed for '{ctx.what}'"))
    failures.raise_if_crashed(driver, ctx, when=when)
    progress.step("procivis's process screen closed without a failure animation")


def run(driver, provider: DeeplinkProvider, credential_name: str, app_package: str,
        pin: str = "", **page_args):
    """Open a credential offer deeplink and accept it in the Procivis wallet.

    Every verdict this flow reaches about procivis is a `base.outcome.FlowFailure` naming what
    happened, and the progress trail records how far procivis got. Taps through a page object
    (Accept) can still raise that page's own generic error when its button cannot be found.

    Args:
        driver:          Appium driver
        provider:        DeeplinkProvider — supplies the deeplink URL for credential_name
        credential_name: Key used to look up the deeplink (e.g. "pid_issuance")
        app_package:     App package (e.g. "ch.procivis.one.wallet.trial")
        pin:             PIN to unlock the app — always required as the app locks on deeplink
        **page_args:     Passed through to page objects (timeouts, debug, etc.)
    """
    ctx = FlowContext(
        flow="credential_flow", wallet=SCREENS.name, what=credential_name,
        app_package=app_package, pin=pin,
        device_pin=page_args.get("device_pin", ""), page_args=page_args,
    )
    ctx.url = provider.get(credential_name)
    t = ctx.timeout("credential_offer")

    logger.info(f"[credential_flow] Opening deeplink for '{credential_name}'")
    try:
        deeplink.fire(driver, ctx.url, app_package)
    except WebDriverException as exc:
        # This used to escape as a bare driver exception. The shared diagnosis names it.
        logger.info(f"[credential_flow] Android would not route the deeplink: {exc.msg}")
        outcome.raise_refused(driver, SCREENS, ctx, exc, expected="credential offer")

    failures.unlock(driver, ctx)

    landed = None
    try:
        WebDriverWait(driver, t).until(
            EC.any_of(
                EC.presence_of_element_located(_offer_id),
                EC.presence_of_element_located(_process_id),
            )
        )
    except TimeoutException:
        # Also where a dead session ends up: `EC.any_of` swallows every WebDriverException, so the
        # wait runs out instead. `_no_offer`'s first probe is what tells the two apart.
        landed = _no_offer(driver, ctx, t, after="the deeplink")

    try:
        direct = landed == "process" or (
            landed is None and wait_present(driver, _process_id, timeout=1))
    except WebDriverException as exc:
        raise failures.lost(ctx, "after the wait for the offer", exc) from exc
    if direct:
        # Named by which of the two matched: waltid lands on CredentialAcceptProcessScreen
        # ("Credential offering") without the offer ever being seen, the others on
        # InvitationProcessScreen.
        shown = _process_screen_name(driver)
        logger.info(f"[credential_flow] App went directly to {shown} — waiting for its result")
        progress.reached(progress.OPEN, f"procivis showed its {shown} before any offer")
        _await_process(driver, ctx, when="as its process screen closed before any offer")
        # Cleared, so the offer should follow. It is looked for rather than tapped blindly: a
        # procivis that went home instead used to surface as Accept's "Element not found".
        try:
            offer = wait_present(driver, _offer_id, timeout=t)
        except WebDriverException as exc:
            raise failures.lost(ctx, "after its process screen closed", exc) from exc
        if not offer and _no_offer(driver, ctx, t, after="its process screen closed") == "process":
            raise FlowFailure(outcome.PROCESSING, (
                f"[credential_flow] procivis showed its process screen again after it had closed, "
                f"with no credential offer, for '{credential_name}'"
            ))

    logger.info("[credential_flow] Credential offer screen — accepting")
    CredentialOfferPage(driver, **page_args).accept()
    # The Accept button lives on the offer screen, so a tap that landed proves it was shown.
    progress.reached(progress.PRESENT, "procivis showed the credential offer")
    progress.step("tapped Accept")

    # Wait for CredentialOfferScreen to disappear — this always happens on success.
    # CredentialAcceptProcessScreen may or may not appear depending on app version.
    try:
        WebDriverWait(driver, t).until(EC.invisibility_of_element_located(_offer_id))
    except TimeoutException:
        # Still on its own offer after Accept, with nothing else shown: no verdict either way.
        raise FlowFailure(outcome.PROCESSING, (
            f"[credential_flow] CredentialOfferScreen did not dismiss after Accept for "
            f"'{credential_name}' — procivis still showed the offer after {t:.0f}s, with no "
            "failure screen and no process screen"
        ))
    except WebDriverException as exc:
        raise failures.lost(ctx, "mid-wait after Accept", exc) from exc

    # If the processing screen appears, let it complete (handles error detection too).
    try:
        processing = wait_present(driver, _process_id, timeout=2)
    except WebDriverException as exc:
        raise failures.lost(ctx, "after Accept", exc) from exc
    if processing:
        logger.info("[credential_flow] Processing screen appeared — waiting for completion")
        _await_process(driver, ctx, when="as its process screen closed after Accept")
    else:
        failures.raise_if_crashed(driver, ctx, when="after its offer closed")
        progress.step("the offer closed with no process screen seen")

    logger.info(f"[credential_flow] Credential '{credential_name}' accepted")
