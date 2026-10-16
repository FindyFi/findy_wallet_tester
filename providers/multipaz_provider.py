import json
import logging
import re
from urllib.parse import urljoin

import requests

from providers.api_provider import raise_for_rejection
from providers.base import DeeplinkProvider
from providers.web_provider import extract_deeplink

logger = logging.getLogger(__name__)

_AUTHORIZATION_CODE = re.compile(r'name="authorizationCode"\s+value="([^"]+)"')
_PERSON_MAP = re.compile(r"let personMap\s*=\s*(\{.*?\});", re.S)
_SCOPE = re.compile(r'let scope\s*=\s*"([^"]*)"')


class MultipazProvider(DeeplinkProvider):
    """Mints a pre-authorized offer from the Multipaz sandbox issuer (issuer.multipaz.org).

    The issuer's own page offers a "Pre-authorized Offer Flow" per credential configuration, and
    it is three plain HTTP steps, no JavaScript involved:

      1. GET  issuer/authorize?configuration_id=…&tx_kind=none
         redirects to the fictional "Utopia" system of record, which asks who you are.
      2. POST records/authorize with the page's authorizationCode and a person.
      3. The redirect lands on a page printing the openid-credential-offer:// link.

    It is a class rather than an `api` or `web` entry because of step 2. The people are listed in
    a JavaScript object (`personMap`), not in the HTML <select>, and their ids are re-encrypted on
    every visit, so no fixed value in config would survive a second run. And which person to pick
    depends on the credential: everyone has a PID, but only some have a driving licence, and
    picking one without leaves an offer that resolves and then has nothing to issue. So a person
    holding a record of the requested kind is preferred, which is what a tester clicking through
    would do.

    Config format (config/providers.json):
        "multipaz_mdl": {
            "type": "multipaz",
            "base_url": "https://issuer.multipaz.org",
            "credentials": {
                "credential_issuance": { "type": "issuance", "configuration_id": "mDL" }
            }
        }
    """

    def __init__(self, base_url: str, credentials: dict):
        self._base_url = base_url.rstrip("/")
        self._credentials = credentials
        self._session = requests.Session()

    def get(self, name: str) -> str:
        cred = self._credentials.get(name, {})
        configuration_id = cred.get("configuration_id")
        if not configuration_id:
            raise ValueError(
                f"No configuration_id configured for credential '{name}' — add it under "
                f"credentials.{name} in the multipaz provider config [no_retry]"
            )

        start = f"{self._base_url}/issuer/authorize"
        logger.info(f"[multipaz_provider] Starting pre-authorized flow for {configuration_id}")
        page = self._session.get(start, params={
            "configuration_id": configuration_id,
            "url_schema": "openid-credential-offer",
            # What the issuer's own form sends; the server keys the pre-authorized branch on it.
            "request_uri": "https://pre-authorize.multipaz.org/",
            "tx_kind": "none",
        }, timeout=30)
        raise_for_rejection(page, f"GET {start}")

        code = _AUTHORIZATION_CODE.search(page.text)
        people = _PERSON_MAP.search(page.text)
        try:
            person_map = json.loads(people.group(1)) if people else None
        except ValueError:
            person_map = None
        if not code or person_map is None:
            raise ValueError(
                f"Multipaz did not show its identity selection page for {configuration_id} "
                f"(got {page.url}) — the flow has changed [no_retry]"
            )
        scope = _SCOPE.search(page.text)
        person, instance = self._choose(person_map, scope.group(1) if scope else "")

        post_url = urljoin(page.url, "authorize")
        done = self._session.post(post_url, data={
            "authorizationCode": code.group(1), "person": person, "instance": instance,
        }, timeout=30)
        raise_for_rejection(done, f"POST {post_url}")

        found = extract_deeplink(done.text)
        if found:
            return found[0]
        raise ValueError(
            f"No credential offer on the page Multipaz finished on ({done.url}) [no_retry]"
        )

    @staticmethod
    def _choose(people: dict, scope: str):
        """(person id, instance id) of the first person holding a record of the page's scope.

        `instances` is empty for the "core" scope, a record every person has (PID, age
        verification), so there the first person will do. For any other scope (mDL) a person
        without instances has nothing to issue — the page's own script disables Select for them —
        so finding nobody who holds one raises instead of minting an offer that cannot complete.
        """
        if not people:
            raise ValueError("Multipaz's system of record lists nobody to issue to [no_retry]")
        for person_id, person in people.items():
            instances = list((person or {}).get("instances") or {})
            if instances:
                return person_id, instances[0]
        if scope != "core":
            raise ValueError(
                f"Nobody in Multipaz's system of record holds a '{scope}' record [no_retry]"
            )
        return next(iter(people)), ""
