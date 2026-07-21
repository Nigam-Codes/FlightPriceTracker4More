"""Amadeus Self-Service Flight Offers Search provider.

Auth: OAuth2 client-credentials. ``AMADEUS_CLIENT_ID`` /
``AMADEUS_CLIENT_SECRET`` are read from the environment (or ``.env``). The
access token is cached in-process and refreshed shortly before it expires.

Rate limits (Self-Service test tier, as documented by Amadeus):
    * ~10 requests/second, and
    * a small **monthly** free quota (varies by API; Flight Offers Search is
      generous but not unlimited).
Every request goes through :func:`request_with_backoff`, which retries on
429/5xx with exponential backoff.

NOTE: the **test** environment returns cached/limited fares. Swap
``AMADEUS_BASE_URL`` to ``https://api.amadeus.com`` with a production key for
live fares.
"""

from __future__ import annotations

import os
import re
import time
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import httpx

from converge_flights.cache import QueryCache
from converge_flights.config import AMADEUS_CABIN, Cabin, CacheConfig, Constraints
from converge_flights.models import Leg, Offer
from converge_flights.providers.base import (
    FlightProvider,
    MissingCredentialsError,
    ProviderError,
    request_with_backoff,
)

DEFAULT_BASE_URL = "https://test.api.amadeus.com"
_TOKEN_PATH = "/v1/security/oauth2/token"
_SEARCH_PATH = "/v2/shopping/flight-offers"
# Refresh the token this many seconds before it actually expires.
_TOKEN_SKEW_SECONDS = 30.0
# ISO-8601 duration as returned by Amadeus itineraries, e.g. "PT4H30M".
_ISO_DURATION = re.compile(r"P(?:(\d+)D)?T(?:(\d+)H)?(?:(\d+)M)?")


def parse_iso_duration_hours(value: str) -> float:
    """Convert an ISO-8601 duration like ``PT4H30M`` to hours (float)."""
    match = _ISO_DURATION.fullmatch(value)
    if match is None:
        raise ValueError(f"Unparseable ISO-8601 duration: {value!r}")
    days = int(match.group(1) or 0)
    hours = int(match.group(2) or 0)
    minutes = int(match.group(3) or 0)
    return days * 24 + hours + minutes / 60.0


class _Token:
    """A cached OAuth2 access token with an absolute expiry."""

    __slots__ = ("value", "expires_at")

    def __init__(self, value: str, expires_at: float) -> None:
        self.value = value
        self.expires_at = expires_at

    def valid(self, now: float) -> bool:
        return now < (self.expires_at - _TOKEN_SKEW_SECONDS)


class AmadeusProvider(FlightProvider):
    """Fetch and normalize round-trip fares from Amadeus."""

    name = "amadeus"

    def __init__(
        self,
        *,
        client: httpx.Client | None = None,
        cache: QueryCache | None = None,
        base_url: str | None = None,
        client_id: str | None = None,
        client_secret: str | None = None,
        raw_sink: list[dict[str, Any]] | None = None,
    ) -> None:
        self._client = client or httpx.Client(timeout=30.0)
        self._owns_client = client is None
        self._cache = cache or QueryCache(CacheConfig(enabled=False))
        self._raw_sink = raw_sink
        self._base_url = (
            base_url or os.environ.get("AMADEUS_BASE_URL") or DEFAULT_BASE_URL
        ).rstrip("/")
        self._client_id = client_id or os.environ.get("AMADEUS_CLIENT_ID")
        self._client_secret = client_secret or os.environ.get("AMADEUS_CLIENT_SECRET")
        self._token: _Token | None = None

    # -- auth ---------------------------------------------------------------

    def _require_credentials(self) -> tuple[str, str]:
        if not self._client_id or not self._client_secret:
            raise MissingCredentialsError(
                "Amadeus provider selected but AMADEUS_CLIENT_ID / "
                "AMADEUS_CLIENT_SECRET are not set. Register a free app at "
                "https://developers.amadeus.com and export both variables "
                "(or add them to your .env)."
            )
        return self._client_id, self._client_secret

    def _access_token(self) -> str:
        now = time.time()
        if self._token is not None and self._token.valid(now):
            return self._token.value

        client_id, client_secret = self._require_credentials()
        response = request_with_backoff(
            self._client,
            "POST",
            f"{self._base_url}{_TOKEN_PATH}",
            data={
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": client_secret,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if response.status_code != 200:
            raise ProviderError(
                f"Amadeus token request failed ({response.status_code}): "
                f"{response.text[:300]}"
            )
        payload = response.json()
        raw_token = payload.get("access_token")
        expires_in = float(payload.get("expires_in", 0))
        if not raw_token:
            raise ProviderError("Amadeus token response missing access_token")
        token = str(raw_token)
        self._token = _Token(token, time.time() + expires_in)
        return token

    # -- search -------------------------------------------------------------

    def _search_params(
        self,
        origin: str,
        destination: str,
        depart: date,
        return_: date,
        constraints: Constraints,
    ) -> dict[str, Any]:
        cabin: Cabin = constraints.cabin
        return {
            "originLocationCode": origin,
            "destinationLocationCode": destination,
            "departureDate": depart.isoformat(),
            "returnDate": return_.isoformat(),
            "adults": 1,
            "currencyCode": constraints.currency,
            "travelClass": AMADEUS_CABIN[cabin],
            # Amadeus supports nonStop=true only; general stop caps are enforced
            # in the normalized filter layer, so we over-fetch and filter there.
            "max": 20,
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
        token = self._access_token()
        response = request_with_backoff(
            self._client,
            "GET",
            f"{self._base_url}{_SEARCH_PATH}",
            params=params,
            headers={"Authorization": f"Bearer {token}"},
        )
        if response.status_code == 200:
            return response.json()
        if response.status_code == 400:
            # Bad/empty route: treat as "no offers" rather than crashing a run.
            return {"data": []}
        raise ProviderError(
            f"Amadeus search failed ({response.status_code}): " f"{response.text[:300]}"
        )

    # -- normalization ------------------------------------------------------

    @staticmethod
    def _parse_leg(itinerary: dict[str, Any], fallback_carrier: str) -> Leg:
        segments = itinerary["segments"]
        first = segments[0]
        last = segments[-1]
        carrier = first.get("carrierCode") or fallback_carrier
        return Leg(
            origin=first["departure"]["iataCode"],
            destination=last["arrival"]["iataCode"],
            depart=datetime.fromisoformat(first["departure"]["at"]),
            arrive=datetime.fromisoformat(last["arrival"]["at"]),
            duration_hours=parse_iso_duration_hours(itinerary["duration"]),
            stops=len(segments) - 1,
            carrier=carrier,
        )

    def normalize(self, payload: Any, *, origin: str, destination: str) -> list[Offer]:
        """Map an Amadeus Flight Offers Search payload to :class:`Offer`."""
        offers: list[Offer] = []
        for item in payload.get("data", []):
            itineraries = item.get("itineraries", [])
            if len(itineraries) < 2:
                continue  # not a round trip
            validating = item.get("validatingAirlineCodes") or [""]
            fallback_carrier = validating[0]
            outbound = self._parse_leg(itineraries[0], fallback_carrier)
            inbound = self._parse_leg(itineraries[1], fallback_carrier)
            price_info = item["price"]
            price = Decimal(str(price_info.get("grandTotal", price_info["total"])))
            offers.append(
                Offer(
                    provider=self.name,
                    origin=origin,
                    destination=destination,
                    price=price,
                    currency=price_info["currency"],
                    outbound=outbound,
                    inbound=inbound,
                    raw_id=str(item.get("id")) if item.get("id") is not None else None,
                )
            )
        return offers

    def close(self) -> None:
        if self._owns_client:
            self._client.close()


__all__ = ["AmadeusProvider"]
