"""Shared plumbing for the plain-code commands (/sector, /stock): a TTL cache with
single-flight refresh and a stale fallback, plus friendly Indonesian API error messages.

It sits on top of app.sectors (the one HTTP client, with its retries and timeout); it never
makes requests itself.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.sectors import SectorsError

log = logging.getLogger(__name__)

# ----------------------------------------------------------------------------- errors


class EmptyData(Exception):
    """The API answered 200 but with nothing we can show."""


def describe_api_error(exc: Exception, not_found: str) -> str:
    """One friendly Indonesian sentence per failure kind. Never includes the API key or
    the raw exception text. `not_found` is the caller's wording for a 404."""
    if isinstance(exc, EmptyData):
        return "Sectors API tidak mengembalikan data untuk permintaan ini."
    if not isinstance(exc, SectorsError):
        return "Terjadi kesalahan tak terduga saat memproses data."
    status = exc.status
    if status is None:
        if "SECTORS_API_KEY" in exc.message:
            return "SECTORS_API_KEY belum diisi, jadi data tidak bisa diambil."
        if exc.timed_out:
            return "Sectors API tidak merespons tepat waktu (timeout). Coba lagi sebentar lagi."
        return "Gagal terhubung ke Sectors API (gangguan jaringan). Coba lagi sebentar lagi."
    if status in (401, 403):
        return f"Akses ke Sectors API ditolak ({status}). Admin perlu memeriksa SECTORS_API_KEY."
    if status == 404:
        return not_found
    if status == 429:
        return "Batas permintaan Sectors API tercapai (429). Coba lagi beberapa menit lagi."
    if status >= 500:
        return f"Server Sectors API sedang bermasalah ({status}). Coba lagi nanti."
    return f"Permintaan ditolak Sectors API ({status})."


def _can_serve_stale(exc: Exception) -> bool:
    """400/404 mean the request itself is wrong: an old copy would hide that."""
    return isinstance(exc, SectorsError) and exc.status not in (400, 404)


# ----------------------------------------------------------------------------- cache


@dataclass
class Fetched[T]:
    value: T
    fetched_at: datetime  # UTC, when the API produced this copy
    from_cache: bool = False
    # None = fresh. "api_failed" = refresh failed, older copy served. "aged" = past its
    # TTL but re-shown for a button tap (taps never spend credits).
    stale_reason: str | None = None


@dataclass
class _Entry:
    value: Any
    fetched_at: datetime
    stored_at: float  # clock() reading, for age maths


class TTLCache:
    """In-process cache with single-flight refresh and a stale fallback.

    Concurrent get()s for the same key share one in-flight request, so ten people
    opening /sector banks at once spend 5 credits, not 50.
    """

    def __init__(
        self, ttl: float, stale_max: float, clock: Callable[[], float] = time.monotonic
    ):
        self.ttl = ttl
        self.stale_max = stale_max
        self._clock = clock
        self._entries: dict[tuple, _Entry] = {}
        self._inflight: dict[tuple, asyncio.Future] = {}

    def _age(self, entry: _Entry) -> float:
        return self._clock() - entry.stored_at

    def peek(self, key: tuple) -> Fetched | None:
        """Cached copy without touching the network, or None once older than stale_max."""
        entry = self._entries.get(key)
        if entry is None or self._age(entry) > self.stale_max:
            return None
        reason = "aged" if self._age(entry) >= self.ttl else None
        return Fetched(entry.value, entry.fetched_at, from_cache=True, stale_reason=reason)

    async def get(self, key: tuple, fetch: Callable[[], Awaitable[Any]]) -> Fetched:
        entry = self._entries.get(key)
        if entry is not None and self._age(entry) < self.ttl:
            return Fetched(entry.value, entry.fetched_at, from_cache=True)

        task = self._inflight.get(key)
        if task is None:
            task = asyncio.ensure_future(self._refresh(key, fetch))
            self._inflight[key] = task
            task.add_done_callback(lambda _t: self._inflight.pop(key, None))
        try:
            # shield: one caller being cancelled must not cancel the shared request
            return await asyncio.shield(task)
        except Exception as e:
            if entry is not None and self._age(entry) <= self.stale_max and _can_serve_stale(e):
                log.warning("refresh of %s failed (%s); serving cached copy", key, e)
                return Fetched(
                    entry.value, entry.fetched_at, from_cache=True, stale_reason="api_failed"
                )
            raise

    async def _refresh(self, key: tuple, fetch) -> Fetched:
        value = await fetch()  # raises -> nothing is stored; EmptyData is never cached
        entry = _Entry(value, datetime.now(UTC), self._clock())
        self._entries[key] = entry
        return Fetched(value, entry.fetched_at)

    def clear(self) -> None:
        self._entries.clear()
