"""Async client for the Sectors Financial API v2, Indonesia (IDX) only.

data = await get_client().get("index_daily", path_params={"index_code": "ihsg"}, params={...})
"""

import asyncio
import logging
from typing import Any

import httpx

from app.config import settings

log = logging.getLogger(__name__)


ENDPOINTS: dict[str, str] = {
    "daily": "/v2/daily/{symbol}/",
    "index_daily": "/v2/index-daily/{index_code}/",
    "idx_total": "/v2/idx-total/",
    "foreign_flow": "/v2/foreign-flow/{symbol}/",
    "company_report": "/v2/company/report/{symbol}/",
    "subsector_report": "/v2/subsector/report/{sub_sector}/",
    "subsectors": "/v2/subsectors/",
    "top_changes": "/v2/companies/top-changes/",
    "most_traded": "/v2/most-traded/",
    "companies": "/v2/companies/",
    "news": "/v2/news/",
    "filings": "/v2/filings/",
}

RETRY_STATUSES = {429, 500, 502, 503, 504}


class SectorsError(Exception):
    def __init__(self, status: int | None, message: str, *, timed_out: bool = False):
        super().__init__(f"[{status}] {message}")
        self.status = status
        self.message = message
        self.timed_out = timed_out  # no response in time (vs. a refused connection)


class SectorsClient:
    def __init__(self, api_key: str, base_url: str):
        if not api_key:
            raise SectorsError(
                None, "SECTORS_API_KEY is empty: paste your key into .env"
            )
        self._http = httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": api_key},  # raw key, no "Bearer"
            timeout=httpx.Timeout(20.0),
        )

    async def get(
        self,
        name: str,
        *,
        path_params: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        path = ENDPOINTS[name].format(**(path_params or {}))
        clean = {k: v for k, v in (params or {}).items() if v is not None}
        log.info("sectors GET %s", path)

        for attempt in range(3):
            try:
                resp = await self._http.get(path, params=clean)
                resp.raise_for_status()
                return resp.json()  # success -> done

            except httpx.HTTPStatusError as e:
                status = e.response.status_code
                if status in RETRY_STATUSES and attempt < 2:
                    wait = float(
                        e.response.headers.get("Retry-After", 1.5 * (attempt + 1))
                    )
                    log.warning(
                        "Sectors %s -> %s, retrying in %.1fs", path, status, wait
                    )
                    await asyncio.sleep(wait)
                    continue
                raise SectorsError(status, _error_text(e.response)) from e

            except (
                httpx.RequestError
            ) as e:  # timeout / network blip: no response at all
                if attempt < 2:
                    await asyncio.sleep(1.5 * (attempt + 1))
                    continue
                timed_out = isinstance(e, httpx.TimeoutException)
                raise SectorsError(
                    None, f"network error: {e!r}", timed_out=timed_out
                ) from e

        raise SectorsError(None, "all retries failed")  # loop fell through

    async def aclose(self) -> None:
        await self._http.aclose()


def _error_text(resp: httpx.Response) -> str:
    hints = {
        400: "bad request - check your params",
        401: "invalid or missing API key",
        404: "not found - check the endpoint name or path params",
        410: "endpoint is gone (are you calling /v1/?)",
    }
    try:
        body = resp.json()
        detail = (
            body.get("message") or body.get("error") or body.get("detail") or str(body)
        )
    except ValueError:
        detail = resp.text[:200]
    hint = hints.get(resp.status_code)
    return f"{detail} ({hint})" if hint else str(detail)


_client: SectorsClient | None = None


def get_client() -> SectorsClient:
    global _client
    if _client is None:
        _client = SectorsClient(
            api_key=settings.sectors_api_key,
            base_url=settings.sectors_api_url,
        )
    return _client


async def close_client() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
