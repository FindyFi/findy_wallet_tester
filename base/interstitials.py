"""Prompts that can interrupt any wait, disposed of by the shared wait loop.

The thing hovi's outcome loop was missing. Its `wait_for` polls for outcome screens and nothing
else, which is why authbound, heidi, gataca and procivis could not use it: roughly half of each of
their hand-written loops is answering a biometric prompt, typing a passcode, accepting a connection
or picking an app from Android's chooser. Left unanswered, any of those stalls the wait and the
verdict reads `absent` — a wrong statement about a wallet that was, in fact, waiting for us.

Each wallet declares an ordered tuple of these once, and every shared wait services them. gataca is
the clearest case: its "device PIN, twice, at two different points" is today two hand-placed
`authenticate_with_pin` calls with a `sleep(2)` in front of each, in two different functions. Here
it is one declaration, serviced wherever the prompt actually appears.
"""
import logging
from dataclasses import dataclass
from typing import Any, Callable, Optional

from base.android import (
    authenticate_with_pin,
    dismiss_biometric_enrollment,
    handle_biometric_if_present,
    handle_permission_if_present,
    in_biometric_enrollment,
)
from base.screens import Probe
from base.utils import wait_present

logger = logging.getLogger(__name__)

# A note left on the context when Android offered to enrol a fingerprint. Only interesting if the
# flow later fails: the wallet may be asking for a credential the device cannot produce.
ENROLLMENT_OFFERED = "biometric-enrollment"


@dataclass(frozen=True)
class Interstitial:
    """One kind of interruption, and how to get past it.

    A single `handle` returning "I acted" rather than a (probe, act) pair, because
    `base/android.py` already ships exactly that shape — `handle_biometric_if_present`,
    `handle_permission_if_present` and `authenticate_with_pin` all detect and act and return bool.
    Screens that are a genuine probe/act pair use `screen_action()` to take the same shape.
    """

    name: str                                          # logged, and the key of the fire budget
    handle: Callable[[Any, Any], bool]                 # (driver, ctx) -> acted?
    max_fires: int = 4
    extends: float = 0.0                               # seconds of deadline bought per fire


def screen_action(name: str, on_screen: Probe, act: Callable[[Any, Any], None], *,
                  timeout: float = 0.5, max_fires: int = 4,
                  extends: float = 0.0) -> Interstitial:
    """An in-app screen that has to be driven before the flow can continue.

    heidi's connection-consent screen, procivis's PIN entry, authbound's wallet passcode.
    """
    def handle(driver, ctx) -> bool:
        if not on_screen(driver, timeout):
            return False
        logger.info(f"[{ctx.flow}] {ctx.wallet}: {name} — handling")
        act(driver, ctx)
        return True

    return Interstitial(name, handle, max_fires=max_fires, extends=extends)


def permission_dialog(*, allow: bool = True, max_fires: int = 3) -> Interstitial:
    """Android's runtime permission dialog.

    It can appear whenever the app first needs the permission, which is not tied to any step a flow
    knows about — hovi's notification prompt is the live case. Four wallets handle it at hand-picked
    call sites today and four never handle it at all.
    """
    return Interstitial(
        "permission-dialog",
        lambda driver, ctx: handle_permission_if_present(driver, allow=allow, detect_timeout=0.4),
        max_fires=max_fires,
    )


def biometric_fingerprint(*, detect_timeout: float = 0.4, max_fires: int = 4) -> Interstitial:
    """The system biometric sheet, answered with a simulated fingerprint (emulator-friendly)."""
    return Interstitial(
        "biometric",
        lambda driver, ctx: handle_biometric_if_present(driver, detect_timeout=detect_timeout),
        max_fires=max_fires,
    )


def device_pin_prompt(*, detect_timeout: float = 0.4, max_fires: int = 4,
                      extends: float = 10.0) -> Interstitial:
    """The system auth sheet, answered with the device lock PIN rather than a fingerprint.

    Preferred where fingerprint simulation is unreliable or locks out. It also absorbs Android's
    biometric-enrolment wizard, which authbound discovered the hard way: the PIN is accepted and
    Android *then* offers to enrol a fingerprint, which makes the dismissal wait fail even though
    authentication succeeded. That says nothing about the wallet, so it must not surface as one.

    `extends` because answering a prompt is progress — the wallet asked us for something and got
    it, so the wait has earned more time.
    """
    def handle(driver, ctx) -> bool:
        try:
            acted = authenticate_with_pin(driver, ctx.device_pin, detect_timeout=detect_timeout)
        except Exception:
            if not in_biometric_enrollment(driver):
                raise
            acted = True
        if in_biometric_enrollment(driver):
            ctx.notes.add(ENROLLMENT_OFFERED)
            dismiss_biometric_enrollment(driver)
            return True
        return acted

    return Interstitial("device-pin", handle, max_fires=max_fires, extends=extends)


def app_chooser(app_label: str, *, max_fires: int = 2) -> Interstitial:
    """Android's "Open with" chooser, shown when several installed wallets claim a scheme.

    Only authbound hits this today, because `openid4vp` is claimed by most of the fleet. Picking the
    wallet under test is not interception: the chooser exists precisely because the URL *is*
    routable here, so answering it measures the wallet rather than the device's app mix.
    """
    from appium.webdriver.common.appiumby import AppiumBy

    item = (AppiumBy.XPATH, f'//*[@text={app_label!r}]')
    once = (AppiumBy.XPATH, '//*[@resource-id="android:id/button_once"]')

    def handle(driver, ctx) -> bool:
        if not wait_present(driver, item, timeout=0.4):
            return False
        logger.info(f"[{ctx.flow}] {ctx.wallet}: app chooser — selecting {app_label}")
        driver.find_element(*item).click()
        if wait_present(driver, once, timeout=1):
            driver.find_element(*once).click()
        return True

    return Interstitial("app-chooser", handle, max_fires=max_fires)


def service(driver, ctx, items, fired: dict) -> Optional[Interstitial]:
    """Give each interstitial one chance to act; return the first that did.

    Order is a per-wallet fact, not a detail: authbound's app chooser has to be answered before any
    outcome screen can even be looked for, because the chooser is what is covering it.

    Budgets are per name so a chatty permission dialog cannot exhaust the budget that belongs to a
    genuine authentication loop. A name over budget stops being serviced and is left for the caller
    to report — that is the difference between "the wallet asked twice" and "the wallet is stuck in
    a prompt loop", which is a real authbound failure mode.
    """
    for item in items:
        if fired.get(item.name, 0) >= item.max_fires:
            continue
        try:
            acted = item.handle(driver, ctx)
        except Exception as exc:
            # An interstitial that throws must not masquerade as the flow's failure.
            logger.warning(f"[{ctx.flow}] {ctx.wallet}: {item.name} handler raised: {exc}")
            continue
        if acted:
            fired[item.name] = fired.get(item.name, 0) + 1
            logger.info(f"[{ctx.flow}] {ctx.wallet}: {item.name} fired "
                        f"({fired[item.name]}/{item.max_fires})")
            return item
    return None


def over_budget(items, fired: dict) -> list:
    """Names that hit their cap — the evidence behind a `prompt_loop` verdict."""
    return [i.name for i in items if fired.get(i.name, 0) >= i.max_fires]
