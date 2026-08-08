"""SerpApi Google Flights provider.

The compliant way to get real, current, web-sourced fares as structured data:
the app calls SerpApi's licensed ``google_flights`` engine and receives Google
Flights results as JSON — no scraping, no ToS violation.

Auth: a single ``SERPAPI_API_KEY`` (query param) read from the environment (or
``.env``). Get a free key (~100 searches/month, no card) at
https://serpapi.com/users/sign_up.

Round trips use Google Flights' own **two-call** model, mirrored by SerpApi:

    1. ``engine=google_flights&type=1`` returns *outbound* options in
       ``best_flights``/``other_flights``. Each carries the **total round-trip**
       ``price``, a ``total_duration`` (minutes), the outbound ``flights[]``
       segments, and a ``departure_token``.
    2. Repeating the call with a ``departure_token`` returns the *return*
       options for that outbound; the selected combination's ``price`` is the
       concrete round-trip total.

To keep the free-tier quota sane we expand only the cheapest outbound option(s)
that pass the outbound-side constraints (a quota hint — final qualification is
still done authoritatively in :mod:`converge_flights.filters`, so every offer is
judged identically to the other providers). Both calls flow through
:class:`QueryCache` so re-runs don't burn quota.

Rate limits: SerpApi enforces a monthly search quota and a concurrency limit;
429/5xx are retried via :func:`request_with_backoff`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import httpx

from converge_flights.cache import QueryCache
from converge_flights.config import SERPAPI_CABIN, CacheConfig, Constraints
from converge_flights.models import Leg, Offer
from converge_flights.providers.base import (
    FlightProvider,
    MissingCredentialsError,
    ProviderError,
    request_with_backoff,
)

DEFAULT_BASE_URL = "https://serpapi.com"
_SEARCH_PATH = "/search"
# How many of the cheapest qualifying outbound options to expand into return
# lookups. 1 keeps a round-trip search to two SerpApi calls (quota-friendly).
DEFAULT_MAX_RETURN_LOOKUPS = 1


def _parse_dt(value: str) -> datetime:
    """Parse a Google Flights local timestamp (``YYYY-MM-DD HH:MM``)."""
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return datetime.strptime(value, "%Y-%m-%d %H:%M")


@dataclass(frozen=True)
class _Option:
    """A normalized one-leg option plus its round-trip price and next token."""

    leg: Leg
    price: Decimal
    token: str | None


class SerpApiProvider(FlightProvider):
    """Fetch and normalize round-trip fares from SerpApi's Google Flights engine."""

    name = "serpapi"

    def __init__(
        self,
        *,
        client: httpx.Client | None = None,
        cache: QueryCache | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
        max_return_lookups: int = DEFAULT_MAX_RETURN_LOOKUPS,
        raw_sink: list[dict[str, Any]] | None = None,
    ) -> None:
        self._client = client or httpx.Client(timeout=30.0)
        self._owns_client = client is None
        self._cache = cache or QueryCache(CacheConfig(enabled=False))
        self._raw_sink = raw_sink
        self._base_url = (
            base_url or os.environ.get("SERPAPI_BASE_URL") or DEFAULT_BASE_URL
        ).rstrip("/")
        self._api_key = api_key or os.environ.get("SERPAPI_API_KEY")
        self._max_return_lookups = max(1, max_return_lookups)

    def _require_key(self) -> str:
        if not self._api_key:
            raise MissingCredentialsError(
                "SerpApi provider selected but SERPAPI_API_KEY is not set. Get a "
                "free key (~100 searches/month, no card) at "
                "https://serpapi.com/users/sign_up and export SERPAPI_API_KEY "
                "(or add it to your .env)."
            )
        return self._api_key

    def _params(
        self,
        origin: str,
        destination: str,
        depart: date,
        return_: date,
        constraints: Constraints,
        *,
        departure_token: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "engine": "google_flights",
            "departure_id": origin,
            "arrival_id": destination,
            "outbound_date": depart.isoformat(),
            "return_date": return_.isoformat(),
            "type": 1,  # round trip
            "travel_class": SERPAPI_CABIN[constraints.cabin],
            "currency": constraints.currency,
            "hl": "en",
        }
        if departure_token is not None:
            params["departure_token"] = departure_token
        return params

    def search(
        self,
        origin: str,
        destination: str,
        depart: date,
        return_: date,
        constraints: Constraints,
    ) -> list[Offer]:
        base = self._params(origin, destination, depart, return_, constraints)
        outbound_payload = self._get(base)
        outbounds = self._parse_options(outbound_payload)
        if not outbounds:
            return []

        offers: list[Offer] = []
        lookups = 0
        for cand in sorted(outbounds, key=lambda o: o.price):
            if lookups >= self._max_return_lookups:
                break
            if cand.token is None or not self._outbound_ok(cand.leg, constraints):
                continue  # quota hint only; filters.py re-checks authoritatively
            return_params = self._params(
                origin,
                destination,
                depart,
                return_,
                constraints,
                departure_token=cand.token,
            )
            return_payload = self._get(return_params)
            lookups += 1
            for ret in self._parse_options(return_payload):
                offers.append(
                    Offer(
                        provider=self.name,
                        origin=origin,
                        destination=destination,
                        price=ret.price,
                        currency=constraints.currency,
                        outbound=cand.leg,
                        inbound=ret.leg,
                    )
                )
        return offers

    def _get(self, params: dict[str, Any]) -> Any:
        api_key = self._require_key()
        # Cache/raw-dump key excludes the secret; the key itself is sent only on
        # the wire.
        cache_key = QueryCache.make_key(self.name, params)
        payload = self._cache.get(cache_key)
        if payload is None:
            response = request_with_backoff(
                self._client,
                "GET",
                f"{self._base_url}{_SEARCH_PATH}",
                params={**params, "api_key": api_key},
                headers={"Accept": "application/json"},
            )
            if response.status_code != 200:
                raise ProviderError(
                    f"SerpApi search failed ({response.status_code}): "
                    f"{response.text[:300]}"
                )
            payload = response.json()
            self._cache.set(cache_key, payload)
        if self._raw_sink is not None:
            self._raw_sink.append({"query": params, "response": payload})
        return payload

    # -- normalization ------------------------------------------------------

    @staticmethod
    def _leg_from(flights: list[dict[str, Any]], total_minutes: float) -> Leg | None:
        if not flights or total_minutes <= 0:
            return None
        first = flights[0]
        last = flights[-1]
        number = str(first.get("flight_number", ""))
        carrier = number.split()[0] if number.split() else str(first.get("airline", ""))
        return Leg(
            origin=first["departure_airport"]["id"],
            destination=last["arrival_airport"]["id"],
            depart=_parse_dt(first["departure_airport"]["time"]),
            arrive=_parse_dt(last["arrival_airport"]["time"]),
            duration_hours=total_minutes / 60.0,
            stops=len(flights) - 1,
            carrier=carrier,
        )

    def _parse_options(self, payload: Any) -> list[_Option]:
        """Normalize best_flights + other_flights into one-leg options."""
        options: list[_Option] = []
        for group in ("best_flights", "other_flights"):
            for item in payload.get(group, []) or []:
                leg = self._leg_from(
                    item.get("flights", []), float(item.get("total_duration", 0))
                )
                price_raw = item.get("price")
                if leg is None or price_raw is None:
                    continue
                options.append(
                    _Option(
                        leg=leg,
                        price=Decimal(str(price_raw)),
                        token=item.get("departure_token"),
                    )
                )
        return options

    @staticmethod
    def _outbound_ok(leg: Leg, constraints: Constraints) -> bool:
        """Cheap outbound-side pre-check used only to limit return lookups."""
        if leg.stops > constraints.max_stops:
            return False
        if leg.duration_hours > constraints.max_duration_hours:
            return False
        window = constraints.depart_time_window
        if window is not None and not (window.start <= leg.depart.time() <= window.end):
            return False
        return True

    def close(self) -> None:
        if self._owns_client:
            self._client.close()


__all__ = ["SerpApiProvider"]
