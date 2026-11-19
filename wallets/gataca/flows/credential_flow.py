import logging
import time

from selenium.common.exceptions import WebDriverException

from base import deeplink, outcome, progress
from base.flow_context import FlowContext
from base.outcome import FlowFailure
from providers.base import DeeplinkProvider
from wallets.gataca.flows import _failures as failures
from wallets.gataca.pages import credential_offer_page as offer_page
from wallets.gataca.pages import error_page, success_page
from wallets.gataca.pages.credential_offer_page import CredentialOfferPage
from wallets.gataca.pages.home_page import on_screen as home_on_screen
from wallets.gataca.pages.success_page import SuccessPage
from wallets.gataca.screens import SCREENS

logger = logging.getLogger(__name__)

_EXPECTED = "credential offer"

# "Login Successful" answered more than this many times is the dialog staying up after its OK.
_MAX_LOGIN_TAPS = 3


def _watch(ctx: FlowContext, state: dict):
    """gataca's screens, as (name, probe, probe timeout). The system auth sheet is a probe that
    answers it: `first_of` then reports it and the caller carries on waiting."""
    auth = ("the system auth sheet",
            lambda d, w: failures.answer_auth(d, ctx, state["when"], detect_timeout=w), 0.4)
    return {
        "auth": auth,
        "error": ("error", error_page.present, 0.5),
        "no match": ("no match", offer_page.no_match, 0.5),
        "rejected": ("rejected", success_page.on_rejected_screen, 0.5),
        "shared": ("Credentials Shared", success_page.on_terminal_screen, 0.5),
        "login": ("Login Successful", success_page.on_login_screen, 0.5),
        # "Sending your consent" lies over home, whose heading stays in the tree beneath it
        # (2.5.9 dumps): so before home, or home passes while gataca is still sending.
        "processing": ("processing", offer_page.processing, 0.5),
        "offer": ("the credential offer", offer_page.on_screen, 1.0),
        "home": ("home", home_on_screen, 1.0),
    }


def _email_unavailable(driver, ctx: FlowContext) -> None:
    """The offer's "Email: Unavailable": ours to provide, so `setup`, and checked before the
    no-match dialog, which it can sit under (2.5.9) and which would publish it as gataca's."""
    if offer_page.email_unavailable(driver, 1.0):
        logger.warning("[credential_flow] Email credential unavailable for active DID")
        raise FlowFailure(outcome.SETUP, (
            "[credential_flow] Email credential not available for the active DID. "
            "Complete the one-time email verification flow manually first: "
            "open the pension issuer → tap 'Get Credentials' → enter email → biometric → OTP."
        ))


def _settle(driver, ctx: FlowContext, t: float) -> None:
    """After OK on "Credentials Shared": wait for gataca's real home, answering the system auth
    sheet that can come up first (procivis, 2026-11-18: two attempts failed on a sheet nobody
    answered after this OK). Returns on home; raises a FlowFailure otherwise."""
    state = {"when": "after Credentials Shared"}
    w = _watch(ctx, state)
    watch = [w["auth"], w["error"], w["home"]]
    head = f"[credential_flow] gataca took the OK on Credentials Shared for '{ctx.what}' and failed"
    while True:
        seen = failures.first_of(driver, ctx, watch, t, during="after Credentials Shared")
        if not seen:
            seen = failures.late_look(driver, ctx, watch[1:], during="after Credentials Shared")
        if seen == "the system auth sheet":
            continue
        if seen == "error":
            raise failures.error_failure(driver, ctx, head)
        if seen == "home":
            if success_page.on_issued_banner(driver, 0.5):
                progress.step("gataca's home showed 'Credential Successfully Issued'")
            else:
                progress.step("gataca returned to its home screen")
            return
        failures.raise_if_crashed(driver, ctx, when="after Credentials Shared")
        raise FlowFailure(outcome.PROCESSING, (
            f"[credential_flow] gataca did not get back to its home screen in the {t:.0f}s after "
            f"the OK on Credentials Shared for '{ctx.what}', so whether it stored the credential "
            "is unknown"
        ))


def _drive(driver, ctx: FlowContext, t: float) -> None:
    """Answer gataca's screens from the deeplink to the end of the issuance, in whatever order they
    come. Returns once gataca has issued (Credentials Shared, settled on home) or gone home after
    Share; raises a FlowFailure otherwise.

    Two phases. Before Share: the system auth sheet (procivis raises it before any offer), the
    error and no-match screens, "Login Successful" (the OIDC step on an authorization_code issuer,
    taken before the offer), "Credentials Shared" (procivis issues directly, with no offer) and the
    offer. After Share: the same, minus the offer, plus home — a bare home is only meaningful
    then, because gataca is already on home when the deeplink is fired.

    The error and no-match screens are left up when raising: init_flow recovers in teardown.
    """
    state = {"when": "after the deeplink"}
    w = _watch(ctx, state)
    head = f"[credential_flow] gataca could not process the credential offer for '{ctx.what}'"
    after_share = f"[credential_flow] gataca took the Share for '{ctx.what}' and then failed"
    shared_tapped, after, in_front = False, "the deeplink", False
    sending_until = time.monotonic() + t

    while True:
        if not shared_tapped:
            watch = [w["auth"], w["error"], w["no match"], w["shared"], w["login"], w["offer"]]
        else:
            watch = [w["auth"], w["error"], w["rejected"], w["no match"], w["shared"], w["login"],
                     w["processing"], w["home"]]
        seen = failures.first_of(driver, ctx, watch, t, during="while waiting for the offer"
                                 if not shared_tapped else "after Share")
        if not seen and not shared_tapped:
            seen = failures.nothing_appeared(
                driver, ctx, late=watch[2:], timeout=t, expected=_EXPECTED,
                waited_for="the credential offer", after=after, head=head,
                was_in_front=in_front, fetching=offer_page.processing)
        elif not seen:
            seen = failures.late_look(driver, ctx, watch[1:] + [w["offer"]], during="after Share")
            if seen == "the credential offer":
                raise FlowFailure(outcome.PROMPT_LOOP, (
                    f"[credential_flow] gataca was showing the credential offer again {t:.0f}s "
                    f"after Share for '{ctx.what}', although it had already been answered, "
                    "instead of progressing"
                ))
            if not seen:
                failures.raise_if_crashed(driver, ctx, when="after Share")
                raise FlowFailure(outcome.PROCESSING, (
                    f"[credential_flow] gataca showed neither Credentials Shared, its home screen "
                    f"nor its error screen in the {t:.0f}s after Share for '{ctx.what}', so "
                    "whether it stored the credential is unknown"
                ))

        if seen == "processing":
            if time.monotonic() >= sending_until:
                progress.reached(progress.OPEN, "gataca was in front, sending its consent")
                raise FlowFailure(outcome.PROCESSING, (
                    f"[credential_flow] gataca was still on 'Sending your consent' {t:.0f}s after "
                    f"its last answer for '{ctx.what}', so whether it stored the credential is "
                    "unknown"
                ))
            continue
        sending_until = time.monotonic() + t
        if seen == "the system auth sheet":
            after = "the system auth sheet"
            continue
        if seen == "error":
            progress.reached(progress.OPEN, "gataca was in front, showing its error screen")
            raise failures.error_failure(driver, ctx, after_share if shared_tapped else head)
        if seen == "rejected":
            progress.reached(progress.OPEN, "gataca was in front, showing its Rejected dialog")
            raise failures.rejected_failure(driver, ctx)
        if seen == "no match":
            _email_unavailable(driver, ctx)
            progress.reached(progress.PRESENT, "gataca showed the request under its no-match dialog")
            raise failures.no_match_failure(driver, ctx, issuance=True)

        in_front = True
        if seen == "Login Successful":
            progress.reached(progress.OPEN, "gataca showed Login Successful")
            taps = ctx.prompt_budget.get("login", 0) + 1
            ctx.prompt_budget["login"] = taps
            if taps > _MAX_LOGIN_TAPS:
                raise FlowFailure(outcome.PROMPT_LOOP, (
                    f"[credential_flow] gataca kept showing Login Successful for '{ctx.what}' "
                    f"after {taps - 1} OK taps instead of progressing"
                ))
            logger.info("[credential_flow] Login Successful (OIDC step, not the credential) "
                        "— confirming and waiting on")
            failures.tap(ctx, "while confirming Login Successful",
                         lambda: SuccessPage(driver, **ctx.page_args).confirm())
            progress.step("confirmed Login Successful (the OIDC step, not the credential)")
            state["when"], after = "after Login Successful", "Login Successful"
            continue

        if seen == "Credentials Shared":
            # authorization_code issuers (e.g. procivis) issue directly after the pre-offer
            # sheet, landing here with no offer to accept.
            progress.reached(progress.OPEN, "gataca showed Credentials Shared")
            logger.info("[credential_flow] Credential issued (Credentials Shared) — tapping OK")
            failures.tap(ctx, "while tapping OK on Credentials Shared",
                         lambda: SuccessPage(driver, **ctx.page_args).confirm())
            progress.reached(progress.ACCEPT, "gataca showed Credentials Shared")
            _settle(driver, ctx, t)
            return

        if seen == "home":
            # Only reached after Share, and after every dialog that overlays home had its look.
            if success_page.on_issued_banner(driver, 0.5):
                progress.step("gataca's home showed 'Credential Successfully Issued'")
            progress.step("gataca returned to its home screen after Share, with no dialog "
                          "(accept not claimed)")
            return

        # The offer.
        progress.reached(progress.OPEN, "gataca showed Sharing requirements")
        _email_unavailable(driver, ctx)
        if offer_page.no_match(driver, 1.0):
            progress.reached(progress.PRESENT, "gataca showed the request under its no-match dialog")
            raise failures.no_match_failure(driver, ctx, issuance=True)
        progress.reached(progress.PRESENT, "gataca showed Sharing requirements")
        logger.info("[credential_flow] Sharing requirements — tapping Share")
        failures.tap(ctx, "while tapping Share", lambda: CredentialOfferPage(driver, **ctx.page_args).accept())
        progress.step("tapped Share")
        shared_tapped, after, state["when"] = True, "Share", "after Share"
        sending_until = time.monotonic() + t


def run(driver, provider: DeeplinkProvider, credential_name: str, app_package: str,
        pin: str = "", **page_args):
    """Open a credential offer deeplink and accept it in the Gataca wallet.

    Flow: deeplink → [system auth sheet] → Sharing requirements → Share → system auth sheet →
    Login Successful → OK → system auth sheet → Credentials Shared → OK → home. Which of those
    appear depends on the issuer (procivis issues straight after the sheet, with no offer).

    Every verdict this flow reaches about gataca is a `base.outcome.FlowFailure` naming what
    happened, and the progress trail records how far gataca got. Taps through a page object can
    still raise that page's own generic error when its button cannot be found.
    """
    device_pin = page_args.get("device_pin", "") or pin
    ctx = FlowContext(
        flow="credential_flow", wallet=SCREENS.name, what=credential_name,
        app_package=app_package, pin=pin, device_pin=device_pin, page_args=page_args,
    )
    ctx.url = provider.get(credential_name)
    t = ctx.timeout("credential_offer")

    logger.info(f"[credential_flow] Opening deeplink for '{credential_name}'")
    try:
        deeplink.fire(driver, ctx.url, app_package)
    except WebDriverException as exc:
        # This used to escape as a bare driver exception. The shared diagnosis names it.
        logger.info(f"[credential_flow] Android would not route the deeplink: {exc.msg}")
        outcome.raise_refused(driver, SCREENS, ctx, exc, expected=_EXPECTED)

    failures.answer_permission(driver, ctx)
    _drive(driver, ctx, t)
    logger.info(f"[credential_flow] Credential flow complete for '{credential_name}'")
