import re
import logging
from io import BytesIO
from typing import Optional
from urllib.parse import urljoin

import requests

from providers.base import DeeplinkProvider

logger = logging.getLogger(__name__)

# Known credential offer URL schemes
_OFFER_SCHEMES = ("openid-credential-offer://", "openid-gatc-credential-offer://", "openid4vp://", "haip://")
_OFFER_PATTERN = re.compile(
    r'(?:openid-credential-offer|openid-gatc-credential-offer|openid4vp|haip)://[^\s"\'<>&]+'
)
# Matches https:// invitation URLs in href attributes (e.g. Paradym verifier pages
# embed the request as a clickable link: <a href="https://paradym.id/invitation?...">)
_HREF_INVITATION_PATTERN = re.compile(
    r'href=["\']([^"\']*(?:request_uri=|credential_offer_uri=)[^"\']+)["\']'
)


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

        url = self._scan_raw_url(resp.text)
        if url:
            logger.info(f"[web_provider] Response body is a direct deeplink URL")
            return url

        url = self._scan_json_uri(resp.text)
        if url:
            logger.info(f"[web_provider] Found deeplink in JSON 'uri' field")
            return url

        url = self._scan_href(resp.text)
        if url:
            logger.info(f"[web_provider] Found deeplink in href attribute")
            return url

        url = self._scan_source(resp.text)
        if url:
            logger.info(f"[web_provider] Found deeplink in page source")
            return url

        url = self._scan_qr_images(resp.text, page_url)
        if url:
            logger.info(f"[web_provider] Found deeplink via QR decode")
            return url

        raise ValueError(
            f"No credential offer deeplink found on: {page_url} — "
            f"expected a bare URL, openid-credential-offer:// in page source, or a QR image"
        )

    def _scan_json_uri(self, text: str) -> Optional[str]:
        """Return the 'uri' field if the response is JSON containing a deeplink.

        Some issuers (e.g. Sphereon) return a JSON object like:
            {"uri": "openid-credential-offer://...", "qrCodeDataUri": "..."}
        """
        stripped = text.strip()
        if not (stripped.startswith("{") or stripped.startswith("[")):
            return None
        try:
            import json
            data = json.loads(stripped)
            uri = data.get("uri", "") if isinstance(data, dict) else ""
            if uri and any(uri.startswith(s) for s in _OFFER_SCHEMES):
                return uri
        except Exception:
            pass
        return None

    def _scan_raw_url(self, text: str) -> Optional[str]:
        """Return the body directly if the entire response is a bare URL.

        Some issuers (e.g. Paradym) respond with a single HTTPS invitation URL
        rather than an HTML page containing a deeplink.
        """
        stripped = text.strip()
        if stripped.startswith(("http://", "https://")) and " " not in stripped and "\n" not in stripped:
            return stripped
        return None

    def _scan_source(self, html: str) -> Optional[str]:
        match = _OFFER_PATTERN.search(html)
        if match:
            return match.group(0).rstrip('"\';>')
        return None

    def _scan_href(self, html: str) -> Optional[str]:
        match = _HREF_INVITATION_PATTERN.search(html)
        if match:
            return match.group(1)
        return None

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
