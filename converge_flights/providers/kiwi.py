"""Kiwi.com (Tequila) Search provider.

Auth: a single ``apikey`` request header. ``KIWI_API_KEY`` is read from the
environment (or ``.env``). Get a free key instantly (no card) at
https://tequila.kiwi.com/portal/login/apikeys.

Rate limits (Tequila Search API):
    * a per-key throughput limit (a few requests/second), and
    * a monthly free quota.
Requests funnel through :func:`request_with_backoff` for 429/5xx retries.

Normalization notes: Kiwi returns a flat ``route`` array where each segment
carries a ``return`` flag (0 = outbound, 1 = inbound). Stops per direction are
therefore ``segment_count - 1`` for that flag. True per-direction durations
come from the ``duration`` object (seconds), *not* from local time deltas —
Kiwi's ``local_departure``/``local_arrival`` are local wall-clock times
formatted with a ``Z`` suffix, so their difference crosses timezones.
"""

from __future__ import annotations

import os
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import httpx

from converge_flights.cache import QueryCache
from converge_flights.config import KIWI_CABIN, Cabin, CacheConfig, Constraints
from converge_flights.models import Leg, Offer
from converge_flights.providers.base import (
    FlightProvider,
    MissingCredentialsError,
    ProviderError,
    request_with_backoff,
)

DEFAULT_BASE_URL = "https://api.tequila.kiwi.com"
_SEARCH_PATH = "/v2/search"


def _parse_local(value: str) -> datetime:
    """Parse a Kiwi ``local_*`` timestamp as a naive local datetime.

    Kiwi formats local wall-clock times with a trailing ``Z``; we strip the
    zone so the value represents local time-of-day for constraint checks.
    """
    cleaned = value.replace("Z", "").replace("z", "")
    parsed = datetime.fromisoformat(cleaned)
    if parsed.tzinfo is not None:
        parsed = parsed.replace(tzinfo=None)
    return parsed


class KiwiProvider(FlightProvider):
    """Fetch and normalize round-trip fares from Kiwi/Tequila."""

    name = "kiwi"

    def __init__(
        self,
        *,
        client: httpx.Client | None = None,
        cache: QueryCache | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
        raw_sink: list[dict[str, Any]] | None = None,
    ) -> None:
        self._client = client or httpx.Client(timeout=30.0)
        self._owns_client = client is None
        self._cache = cache or QueryCache(CacheConfig(enabled=False))
        self._raw_sink = raw_sink
        self._base_url = (
            base_url or os.environ.get("KIWI_BASE_URL") or DEFAULT_BASE_URL
        ).rstrip("/")
        self._api_key = api_key or os.environ.get("KIWI_API_KEY")

    def _require_key(self) -> str:
        if not self._api_key:
            raise MissingCredentialsError(
                "Kiwi provider selected but KIWI_API_KEY is not set. Get a "
                "free key (no card) at "
                "https://tequila.kiwi.com/portal/login/apikeys and export "
                "KIWI_API_KEY (or add it to your .env)."
            )
        return self._api_key

    def _search_params(
        self,
        origin: str,
        destination: str,
        depart: date,
        return_: date,
        constraints: Constraints,
    ) -> dict[str, Any]:
        cabin: Cabin = constraints.cabin
        d = depart.strftime("%d/%m/%Y")
        r = return_.strftime("%d/%m/%Y")
        return {
            "fly_from": origin,
            "fly_to": destination,
            "date_from": d,
            "date_to": d,
            "return_from": r,
            "return_to": r,
            "flight_type": "round",
            "adults": 1,
            "curr": constraints.currency,
            "selected_cabins": KIWI_CABIN[cabin],
            # Over-fetch on stops; the normalized filter layer enforces the cap.
            "max_stopovers": max(constraints.max_stops + 1, 2),
            "limit": 50,
            "sort": "price",
        }

    def search(
        self,
        origin: str,
        destination: str,
        depart: date,
        return_: date,
        constraints: Constraints,
    ) -> list[Offer]:
        params = self._search_params(origin, destination, depart, return_, constraints)
        cache_key = QueryCache.make_key(self.name, params)
        payload = self._cache.get(cache_key)
        if payload is None:
            payload = self._fetch(params)
            self._cache.set(cache_key, payload)
        if self._raw_sink is not None:
            self._raw_sink.append({"query": params, "response": payload})
        return self.normalize(payload, origin=origin, destination=destination)

    def _fetch(self, params: dict[str, Any]) -> Any:
        api_key = self._require_key()
        response = request_with_backoff(
            self._client,
            "GET",
            f"{self._base_url}{_SEARCH_PATH}",
            params=params,
            headers={"apikey": api_key, "accept": "application/json"},
        )
        if response.status_code == 200:
            return response.json()
        raise ProviderError(
            f"Kiwi search failed ({response.status_code}): {response.text[:300]}"
        )

    # -- normalization ------------------------------------------------------

    @staticmethod
    def _build_leg(segments: list[dict[str, Any]], duration_seconds: float) -> Leg | None:
        if not segments or duration_seconds <= 0:
            return None
        first = segments[0]
        last = segments[-1]
        return Leg(
            origin=first["flyFrom"],
            destination=last["flyTo"],
            depart=_parse_local(first["local_departure"]),
            arrive=_parse_local(last["local_arrival"]),
            duration_hours=duration_seconds / 3600.0,
            stops=len(segments) - 1,
            carrier=first.get("airline", ""),
        )

    def normalize(self, payload: Any, *, origin: str, destination: str) -> list[Offer]:
        """Map a Tequila Search payload to :class:`Offer`."""
        top_currency = payload.get("currency", "")
        offers: list[Offer] = []
        for item in payload.get("data", []):
            route = item.get("route", [])
            outbound_segs = [s for s in route if s.get("return", 0) == 0]
            inbound_segs = [s for s in route if s.get("return", 0) == 1]
            duration = item.get("duration", {})
            outbound = self._build_leg(outbound_segs, float(duration.get("departure", 0)))
            inbound = self._build_leg(inbound_segs, float(duration.get("return", 0)))
            if outbound is None or inbound is None:
                continue  # not a usable round trip
            offers.append(
                Offer(
                    provider=self.name,
                    origin=origin,
                    destination=destination,
                    price=Decimal(str(item["price"])),
                    currency=item.get("currency", top_currency),
                    outbound=outbound,
                    inbound=inbound,
                    raw_id=str(item.get("id")) if item.get("id") is not None else None,
                )
            )
        return offers

    def close(self) -> None:
        if self._owns_client:
            self._client.close()


__all__ = ["KiwiProvider"]
