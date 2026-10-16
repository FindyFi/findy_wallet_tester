import html as _html
import json as _json
import re
import logging
from io import BytesIO
from typing import Optional, Tuple
from urllib.parse import urljoin

import requests

from providers.base import DeeplinkProvider

logger = logging.getLogger(__name__)

# Known credential offer URL schemes
_OFFER_SCHEMES = (
    "openid-credential-offer://",
    "openid-gatc-credential-offer://",
    "openid4vp://",
    "openid-vp://",
    "openid-gatc-vp://",
    "haip://",
)
_OFFER_PATTERN = re.compile(
    r'(?:openid-credential-offer|openid-gatc-credential-offer|openid4vp|openid-vp|openid-gatc-vp|haip)://[^\s"\'<>]+'
)
# Matches invitation URLs in href attributes, for verifier pages that link the request rather than
# printing it in a scheme _OFFER_PATTERN knows (e.g. <a href="https://…/invitation?request_uri=…">)
_HREF_INVITATION_PATTERN = re.compile(
    r'href=["\']([^"\']*(?:request_uri=|credential_offer_uri=)[^"\']+)["\']'
)


def _scan_raw_url(text: str) -> Optional[str]:
    """Return the body directly if the entire response is a bare URL.

    Some issuers respond with a single https:// invitation URL rather than an
    HTML page containing a deeplink.

    A body that is a bare openid-credential-offer:// URL needs no branch here — `_scan_source`
    already matches it, and adding one would only change which reason gets logged for the
    pension-demo issuers that answer that way.
    """
    stripped = text.strip()
    if stripped.startswith(("http://", "https://")) and " " not in stripped and "\n" not in stripped:
        return stripped
    return None


def _scan_json_uri(text: str, field: str = "uri") -> Optional[str]:
    """Return `field` from a JSON response body when it holds a deeplink.

    Some providers return a JSON object rather than a page or a bare URL:
        Sphereon  {"uri": "openid-credential-offer://...", "qrCodeDataUri": "..."}
        EUDIPLO   {"uri": "openid4vp://...", "crossDeviceUri": "...", "sessionId": "..."}

    `field` names which key carries the deeplink, so a provider whose response spells it
    differently (or offers several, like EUDIPLO's same-device vs cross-device URIs) can say
    so in config instead of needing its own provider class.

    A dotted `field` walks into nested objects. iGrant's verifier answers
    `{"by_value": {"uri": …}, "by_reference": {"uri": …}}`, and only `by_reference.uri` is a
    request a wallet should accept: the by-value link carries an unsigned request under an
    `x509_hash` client id.
    """
    uri = _json_field(text, field)
    if isinstance(uri, str) and any(uri.startswith(s) for s in _OFFER_SCHEMES):
        return uri
    return None


def _json_field(text: str, field: str):
    """The value at a dotted `field` of a JSON object body, or None if there is none."""
    stripped = text.strip()
    if not stripped.startswith("{"):
        return None
    try:
        value = _json.loads(stripped)
    except ValueError:
        return None
    for key in field.split("."):
        value = value.get(key) if isinstance(value, dict) else None
    return value


# A complete character reference: named (&amp;), decimal (&#38;) or hex (&#x26;), semicolon included.
_ENTITY = re.compile(r"&(?:[A-Za-z][A-Za-z0-9]*|#[0-9]+|#[xX][0-9A-Fa-f]+);")


def _unescape_entities(text: str) -> str:
    """Decode complete HTML character references, and nothing else.

    Not `html.unescape`: that also expands the legacy references that need no semicolon, so a
    raw `&registration=` in a URL came out as `®istration=` and `&notify=` as `¬ify=`.
    `registration` is a real OID4VP parameter, and a browser reading the same attribute leaves
    both alone. Requiring the semicolon decodes what a page actually encoded (`&amp;`) and leaves
    a bare `&` as the separator it is.
    """
    return _ENTITY.sub(lambda m: _html.unescape(m.group(0)), text)


def _scan_source(html: str) -> Optional[str]:
    match = _OFFER_PATTERN.search(html)
    if match:
        return _unescape_entities(match.group(0).rstrip('"\';>'))
    return None


def _scan_href(html: str) -> Optional[str]:
    """Return an invitation URL embedded in an href, with HTML entities decoded.

    The unescape is not cosmetic. An href is attribute text, so a page that writes
    `?client_id=x&request_uri=y` correctly encodes the separator as `&amp;` — Talao's sandbox
    does. Handing that through verbatim yields a URL whose second parameter is named
    `amp;request_uri`, which no wallet recognises, and the failure surfaces as the verifier
    rejecting the request rather than as the harness mangling it.
    """
    match = _HREF_INVITATION_PATTERN.search(html)
    if match:
        return _unescape_entities(match.group(1))
    return None


def _as_json(text: str):
    """The body parsed as JSON when it is a JSON object or array, else None."""
    stripped = text.strip()
    if not (stripped.startswith("{") or stripped.startswith("[")):
        return None
    try:
        return _json.loads(stripped)
    except ValueError:
        return None


def _scan_json_values(data) -> Optional[str]:
    """The first deeplink among a parsed JSON body's string values, depth-first in document order.

    For JSON that names its deeplink with a key other than the one configured. paradym answers
    `{"offerUri": "https://paradym.id/…", "offerDeeplinkUri": "openid-credential-offer://…", …}`,
    and only the second is something a wallet can open; the https envelopes beside it are not.

    Searching the decoded values rather than the raw body is the point. In the raw text a sibling
    URL is still JSON-escaped (`\\u0026` for `&`, `\\/` for `/`), and a scan of it handed wallets a
    malformed request, so a config mistake showed up as a wallet failure.
    """
    stack = [data]
    while stack:
        value = stack.pop()
        if isinstance(value, str):
            match = _OFFER_PATTERN.search(value)
            if match:
                return match.group(0)
        elif isinstance(value, dict):
            stack.extend(reversed(list(value.values())))
        elif isinstance(value, list):
            stack.extend(reversed(value))
    return None


def extract_deeplink(text: str, json_field: str = "uri") -> Optional[Tuple[str, str]]:
    """Find a deeplink in a response body; return (url, how it was found) or None.

    Shared by `WebDeeplinkProvider`, which GETs a page, and `ApiDeeplinkProvider`, which POSTs
    to an endpoint — the shapes a provider answers in (bare URL, JSON field, HTML source, href)
    do not depend on which verb fetched them, and having one place to add a new shape keeps the
    two from drifting apart the way the per-wallet test modules once did.

    QR decoding is deliberately not here: it needs the HTTP session and the page URL to resolve
    <img> sources against, so it stays on the class that has both.

    A JSON body is read as JSON only: first the named field, then any deeplink among its decoded
    values. It never falls through to the page-source and href scans, which would read its text
    still JSON-escaped (see `_scan_json_values`).
    """
    url = _scan_raw_url(text)
    if url:
        return url, "Response body is a direct deeplink URL"

    data = _as_json(text)
    if data is not None:
        url = _scan_json_uri(text, json_field)
        if url:
            return url, f"Found deeplink in JSON '{json_field}' field"
        url = _scan_json_values(data)
        return (url, "Found deeplink among the JSON values") if url else None

    for how, url in (
        ("Found deeplink in page source", _scan_source(text)),
        ("Found deeplink in href attribute", _scan_href(text)),
    ):
        if url:
            return url, how
    return None


class WebDeeplinkProvider(DeeplinkProvider):
    """Fetches a credential offer deeplink from an issuer webpage.

    Instantiated per issuer via factory.get_provider(config, issuer_name).

    Config format (test_cases section):
        "test_cases": {
            "my_issuer": {
                "base_url": "https://issuer.example.com",
                "credentials": {
                    "pid": { "type": "issuance", "path": "pensioncredential.json" }
                }
            }
        }

    Discovery strategies (tried in order):
      1. Regex scan of page HTML for openid-credential-offer:// / openid4vp:// URLs
      2. QR code images on the page decoded with pyzbar
         (optional — install with: pip install pyzbar Pillow)
    """

    def __init__(self, base_url: str, credentials: dict):
        self._base_url = base_url.rstrip("/")
        self._credentials = credentials
        self._session = requests.Session()

    def get(self, name: str) -> str:
        cred = self._credentials.get(name, {})
        path = cred.get("path")
        if not path:
            raise ValueError(
                f"No path configured for credential '{name}' — "
                f"add 'path' under credentials.{name} in the issuer config"
            )

        page_url = f"{self._base_url}/{path.lstrip('/')}"
        logger.info(f"[web_provider] Fetching: {page_url}")
        resp = self._session.get(page_url, timeout=15)
        resp.raise_for_status()

        found = extract_deeplink(resp.text)
        if found:
            url, how = found
            logger.info(f"[web_provider] {how}")
            return url

        url = self._scan_qr_images(resp.text, page_url)
        if url:
            logger.info(f"[web_provider] Found deeplink via QR decode")
            return url

        raise ValueError(
            f"No credential offer deeplink found on: {page_url} — "
            f"expected a bare URL, openid-credential-offer:// in page source, or a QR image [no_retry]"
        )

    def _scan_qr_images(self, html: str, base_url: str) -> Optional[str]:
        try:
            from pyzbar.pyzbar import decode as qr_decode
            from PIL import Image
        except ImportError:
            logger.debug("[web_provider] pyzbar/Pillow not installed — skipping QR scan")
            return None

        img_urls = re.findall(r'<img[^>]+src=["\']([^"\']+)["\']', html)
        for img_path in img_urls:
            img_url = urljoin(base_url, img_path)
            try:
                resp = self._session.get(img_url, timeout=10)
                img = Image.open(BytesIO(resp.content))
                for decoded in qr_decode(img):
                    text = decoded.data.decode("utf-8")
                    if any(text.startswith(s) for s in _OFFER_SCHEMES):
                        return text
            except Exception as e:
                logger.debug(f"[web_provider] Could not decode QR from {img_url}: {e}")

        return None
