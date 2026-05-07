import logging

from providers.base import DeeplinkProvider

logger = logging.getLogger(__name__)


def run(driver, provider: DeeplinkProvider, credential_name: str, app_package: str,
        pin: str = "", **page_args):
    """Credential issuance via deeplink is not supported by UniMe.

    UniMe v0.13.0 does not register any Android intent filter for
    openid-credential-offer:// or any other OID4VCI URI scheme.
    The app exclusively receives credential offers via its built-in
    QR code scanner (Scan tab). Deeplink-based automated issuance
    is therefore not possible with this wallet.

    TODO: Implement QR code injection via the emulator virtual camera
    (virtualscene-image) if automated issuance testing is required.
    """
    provider.get(credential_name)  # fetch URL so the session is consumed cleanly
    raise NotImplementedError(
        "[credential_flow] UniMe does not handle openid-credential-offer:// deeplinks — "
        "the app has no Android intent filter for this scheme.  "
        "Credential offers must be received via the in-app QR code scanner.  "
        "TODO: implement QR code injection via emulator virtualscene-image."
    )
