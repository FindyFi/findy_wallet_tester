import json
import logging
import time

import requests
import websocket

from providers.base import DeeplinkProvider

logger = logging.getLogger(__name__)


class ItbProvider(DeeplinkProvider):
    """Starts an ITB test session and extracts the credential offer deeplink via WebSocket.

    Authenticates to itb.ilabs.ai, initiates a test session for the configured test case,
    waits over WebSocket for the credential offer / verification request deeplink, then
    acknowledges the step so ITB can proceed with validation.

    Config format (test_cases section):
        "test_cases": {
            "itb_diipv5": {
                "type": "itb",
                "base_url": "https://itb.ilabs.ai",
                "username": "${ITB_USERNAME}",
                "password": "${ITB_PASSWORD}",
                "system_id": 79,
                "credentials": {
                    "pid_issuance": {
                        "type": "issuance",
                        "test_case_id": 113,
                        "spec_id": 22,
                        "actor_id": 61,
                        "provide_step": "1.2.4"
                    }
                }
            }
        }

    system_id is set at the issuer level (one per wallet) so the correct registered wallet
    is always used in ITB — never the Mock Personal Wallet.

    Key IDs (DIIPv5 on itb.ilabs.ai):
        spec_id      = 22   (DIIPv5 specification)
        actor_id     = 61   (Personal Wallet / User)
        test_case_id = 113  (VCI Authorization Code flow SD-JWT)
        system_id         varies per wallet (FindyNet org, itb.ilabs.ai):
            Heidi Wallet - Android       : 79
            Heidi Wallet - iOS           : 60
            Paradym Wallet - Android     : 80
            Paradym Wallet - iOS         : 62
            Procivis One Wallet - Android: 105
            Procivis One Wallet - iOS    : 104
            TOPPAN SuperApp - Android    : 82
            TOPPAN SuperApp - iOS        : 59
            Gataca - Android             : 78
            Gataca - iOS                 : 61
            Hovi Wallet - Android        : 106
            Hovi Wallet - iOS            : 103
            Sphereon Wallet - Android    : 81
            Sphereon Wallet - iOS        : 63
            UniMe - Android              : 83
            UniMe - iOS                  : 64
            Mock Personal Wallet         : 37
            Mock Organizational Wallet   : 38
    """

    def __init__(self, base_url: str, system_id: int, credentials: dict, username: str, password: str):
        self._base_url = base_url.rstrip("/")
        self._system_id = system_id
        self._credentials = credentials
        self._session = requests.Session()
        self._token = self._login(username, password)
        self._session.headers.update({
            "Authorization": f"Bearer {self._token}",
            "X-Requested-With": "XMLHttpRequest",
        })
        self._session.cookies.set("tat", self._token)
        self.last_tx_code: str | None = None

    def _login(self, username: str, password: str) -> str:
        resp = self._session.post(
            f"{self._base_url}/api/oauth/access_token",
            data={"email": username, "password": password},
            timeout=15,
        )
        resp.raise_for_status()
        token = resp.json()["access_token"]
        logger.info("[itb_provider] Logged in successfully")
        return token

    def get(self, name: str) -> str:
        cred = self._credentials.get(name, {})
        if not all([cred.get("test_case_id"), cred.get("actor_id"), cred.get("spec_id")]):
            raise ValueError(
                f"[itb_provider] Missing test_case_id/actor_id/spec_id "
                f"for credential '{name}'"
            )

        max_attempts = 3
        last_exc: Exception | None = None
        for attempt in range(1, max_attempts + 1):
            if attempt > 1:
                logger.warning(
                    f"[itb_provider] Retry {attempt - 1}/{max_attempts - 1} "
                    f"for '{name}' after: {last_exc}"
                )
                time.sleep(3)
            try:
                return self._run_session(cred)
            except (RuntimeError, TimeoutError, requests.HTTPError) as exc:
                last_exc = exc

        raise last_exc

    def _run_session(self, cred: dict) -> str:
        test_case_id = cred.get("test_case_id")
        actor_id     = cred.get("actor_id")
        spec_id      = cred.get("spec_id")
        provide_step = cred.get("provide_step", "1.2.4")
        system_id    = self._system_id

        # 1. Initiate — returns plain-text session UUID
        resp = self._session.post(
            f"{self._base_url}/api/tests/{test_case_id}/initiate", timeout=15
        )
        resp.raise_for_status()
        session_id = resp.text.strip().strip('"')
        logger.info(f"[itb_provider] Session started: {session_id}")

        # 2. Configure
        self._session.post(
            f"{self._base_url}/api/tests/{session_id}/configure",
            params={"spec_id": spec_id, "system_id": system_id, "actor_id": actor_id},
            timeout=15,
        ).raise_for_status()

        # 3. Create report record
        self._session.post(
            f"{self._base_url}/api/reports/create",
            data={
                "session_id": session_id,
                "system_id": system_id,
                "actor_id": actor_id,
                "test_id": test_case_id,
            },
            timeout=15,
        ).raise_for_status()

        # 4. Open WebSocket BEFORE starting — ensures we don't miss interactions
        #    that arrive within milliseconds of start (e.g. pre-auth flows).
        ws_url = (
            self._base_url
            .replace("https://", "wss://")
            .replace("http://", "ws://")
            + "/api/ws"
        )
        ws = websocket.create_connection(ws_url, cookie=f"tat={self._token}", timeout=30)
        ws.send(json.dumps({"command": "register", "sessionId": session_id}))

        # 5. Start test execution
        self._session.post(
            f"{self._base_url}/api/tests/{session_id}/start", timeout=15
        ).raise_for_status()

        # 6. Wait for deeplink over WebSocket
        self.last_tx_code = None
        deeplink = self._wait_for_deeplink(ws, session_id)

        # 7. Acknowledge the step — tells ITB the user has "scanned" the QR
        if provide_step:
            self._session.post(
                f"{self._base_url}/api/tests/{session_id}/provide",
                data={"teststep": provide_step, "inputs": "[]"},
                timeout=15,
            )

        return deeplink

    def _wait_for_deeplink(self, ws, session_id: str, timeout: int = 60) -> str:
        """Wait for the qrCodeText interaction on an already-connected WebSocket.

        Filters messages to session_id via tcInstanceId so messages from
        concurrent sessions on the same account are ignored.
        """
        try:
            deadline = time.time() + timeout
            while time.time() < deadline:
                remaining = deadline - time.time()
                ws.settimeout(min(remaining, 5))
                try:
                    msg = ws.recv()
                except websocket.WebSocketTimeoutException:
                    ws.send(json.dumps({"command": "ping"}))
                    continue

                try:
                    data = json.loads(msg)
                except Exception:
                    logger.debug(f"[itb_provider] non-JSON ws msg: {repr(msg)[:80]}")
                    continue

                # Ignore messages from other active sessions on the same account.
                msg_session = data.get("tcInstanceId")
                if msg_session is not None and msg_session != session_id:
                    continue

                logger.debug(
                    f"[itb_provider] ws: step={data.get('stepId')} "
                    f"status={data.get('status')} interact={bool(data.get('interactions'))}"
                )

                # Fail fast if ITB reports a session error for our test.
                if data.get("status") == 3 and msg_session == session_id:
                    raise RuntimeError(
                        f"[itb_provider] ITB test session failed at step "
                        f"{data.get('stepId')!r} for session {session_id}"
                    )

                deeplink = None
                tx_code = None
                for interaction in data.get("interactions") or []:
                    if interaction.get("name") == "qrCodeText":
                        deeplink = interaction["value"]
                    elif interaction.get("name") == "pin":
                        tx_code = interaction["value"]
                if deeplink:
                    _valid = ("openid-credential-offer://", "openid4vp://", "haip://")
                    if not any(deeplink.startswith(s) for s in _valid):
                        raise RuntimeError(
                            f"[itb_provider] Malformed deeplink received: {deeplink!r}"
                        )
                    self.last_tx_code = tx_code
                    logger.info(
                        "[itb_provider] Deeplink received"
                        + (" (tx_code present)" if tx_code else "")
                    )
                    return deeplink
        finally:
            ws.close()

        raise TimeoutError(
            f"[itb_provider] No deeplink received within {timeout}s "
            f"for session {session_id}"
        )
