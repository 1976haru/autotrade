from datetime import datetime, timedelta, timezone

import httpx


PAPER_HOST = "https://openapivts.koreainvestment.com:29443"
LIVE_HOST  = "https://openapi.koreainvestment.com:9443"

_TOKEN_REFRESH_MARGIN = timedelta(seconds=60)


class KisAuthError(RuntimeError):
    pass


class KisApiError(RuntimeError):
    pass


class KisClient:
    """Token-managing async HTTP client for KIS REST API.

    Scope is intentionally narrow — currently only quote (get_price) is
    implemented. Balance/position/order calls land in follow-up PRs once
    SHADOW-mode read paths are validated.

    Tests inject a custom httpx transport via the `transport` kwarg.
    """

    def __init__(
        self,
        app_key:    str,
        app_secret: str,
        is_paper:   bool = True,
        transport:  httpx.AsyncBaseTransport | None = None,
        timeout:    float = 10.0,
    ):
        if not app_key or not app_secret:
            raise KisAuthError("KIS app_key and app_secret are required")
        self.app_key    = app_key
        self.app_secret = app_secret
        self.is_paper   = is_paper
        self.base_url   = PAPER_HOST if is_paper else LIVE_HOST
        self._transport = transport
        self._timeout   = timeout
        self._token: str | None = None
        self._token_expires_at: datetime | None = None

    def _client(self) -> httpx.AsyncClient:
        kwargs = {"base_url": self.base_url, "timeout": self._timeout}
        if self._transport is not None:
            kwargs["transport"] = self._transport
        return httpx.AsyncClient(**kwargs)

    async def _ensure_token(self) -> str:
        now = datetime.now(timezone.utc)
        if self._token and self._token_expires_at and now < self._token_expires_at:
            return self._token

        async with self._client() as client:
            r = await client.post(
                "/oauth2/tokenP",
                json={
                    "grant_type": "client_credentials",
                    "appkey":     self.app_key,
                    "appsecret":  self.app_secret,
                },
            )
        if r.status_code != 200:
            raise KisAuthError(f"KIS token endpoint returned {r.status_code}: {r.text[:200]}")
        data = r.json()
        token = data.get("access_token")
        if not token:
            raise KisAuthError(f"KIS token response missing access_token: {data}")
        self._token = token
        expires_in = int(data.get("expires_in", 86400))
        self._token_expires_at = now + timedelta(seconds=expires_in) - _TOKEN_REFRESH_MARGIN
        return token

    async def get_price(self, symbol: str) -> dict:
        """Returns raw JSON from KIS quote endpoint. Caller extracts fields."""
        token = await self._ensure_token()
        async with self._client() as client:
            r = await client.get(
                "/uapi/domestic-stock/v1/quotations/inquire-price",
                params={
                    "FID_COND_MRKT_DIV_CODE": "J",
                    "FID_INPUT_ISCD":         symbol,
                },
                headers={
                    "authorization": f"Bearer {token}",
                    "appkey":        self.app_key,
                    "appsecret":     self.app_secret,
                    "tr_id":         "FHKST01010100",
                    "custtype":      "P",
                },
            )
        if r.status_code != 200:
            raise KisApiError(f"KIS quote endpoint returned {r.status_code}: {r.text[:200]}")
        return r.json()
