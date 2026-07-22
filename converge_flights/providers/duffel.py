"""Duffel flight-search provider.

Auth: a single bearer token (``DUFFEL_API_TOKEN``) read from the environment
(or ``.env``), plus a ``Duffel-Version`` header. Get a free key with an instant
**test** token (no card) at https://app.duffel.com — test tokens return
sandbox content; live tokens return real airline + NDC fares.

Duffel's search is a two-step **Offer Request → Offers** flow, collapsed here
into one call by posting ``/air/offer_requests?return_offers=true``:

    * one *slice* per direction (origin→destination on depart, the reverse on
      return_), a single adult passenger, and the requested cabin class;
    * the response embeds ``data.offers``, each already priced.

Rate limits (Duffel, as documented): per-token, per-endpoint limits (offer
requests are the tightest — a few hundred/minute) plus 429s under burst; every
call funnels through :func:`request_with_backoff` for 429/5xx retries.

Normalization notes: Duffel offers carry ``total_amount``/``total_currency``
and two ``slices``; each slice has ``segments`` (stops = len-1), an ISO-8601
``duration`` (reused via :func:`parse_iso_duration_hours`, same format as
Amadeus), and per-segment local ``departing_at``/``arriving_at`` and
``marketing_carrier``. Constraints are **not** applied here — that stays in
:mod:`converge_flights.filters`, so filtering is identical across providers.
"""

from __future__ import annotations

import os
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import httpx

from converge_flights.cache import QueryCache
from converge_flights.config import DUFFEL_CABIN, Cabin, CacheConfig, Constraints
from converge_flights.models import Leg, Offer
from converge_flights.providers.amadeus import parse_iso_duration_hours
from converge_flights.providers.base import (
    FlightProvider,
    MissingCredentialsError,
    ProviderError,
    request_with_backoff,
)

DEFAULT_BASE_URL = "https://api.duffel.com"
DEFAULT_VERSION = "v2"
_OFFER_REQUEST_PATH = "/air/offer_requests"


class DuffelProvider(FlightProvider):
    """Fetch and normalize round-trip fares from Duffel."""

    name = "duffel"

    def __init__(
        self,
        *,
        client: httpx.Client | None = None,
        cache: QueryCache | None = None,
        base_url: str | None = None,
        api_token: str | None = None,
        version: str | None = None,
        raw_sink: list[dict[str, Any]] | None = None,
    ) -> None:
        self._client = client or httpx.Client(timeout=30.0)
        self._owns_client = client is None
        self._cache = cache or QueryCache(CacheConfig(enabled=False))
        self._raw_sink = raw_sink
        self._base_url = (
            base_url or os.environ.get("DUFFEL_BASE_URL") or DEFAULT_BASE_URL
        ).rstrip("/")
        self._version = version or os.environ.get("DUFFEL_VERSION") or DEFAULT_VERSION
        self._api_token = api_token or os.environ.get("DUFFEL_API_TOKEN")

    def _require_token(self) -> str:
        if not self._api_token:
            raise MissingCredentialsError(
                "Duffel provider selected but DUFFEL_API_TOKEN is not set. Get a "
                "free test token (no card) at https://app.duffel.com and export "
                "DUFFEL_API_TOKEN (or add it to your .env)."
            )
        return self._api_token

    def _search_body(
        self,
        origin: str,
        destination: str,
        depart: date,
        return_: date,
        constraints: Constraints,
    ) -> dict[str, Any]:
        cabin: Cabin = constraints.cabin
        return {
            "data": {
                "slices": [
                    {
                        "origin": origin,
                        "destination": destination,
                        "departure_date": depart.isoformat(),
                    },
                    {
                        "origin": destination,
                        "destination": origin,
                        "departure_date": return_.isoformat(),
                    },
                ],
                "passengers": [{"type": "adult"}],
                "cabin_class": DUFFEL_CABIN[cabin],
            }
        }

    def search(
        self,
        origin: str,
        destination: str,
        depart: date,
        return_: date,
        constraints: Constraints,
    ) -> list[Offer]:
        body = self._search_body(origin, destination, depart, return_, constraints)
        cache_key = QueryCache.make_key(self.name, body)
        payload = self._cache.get(cache_key)
        if payload is None:
            payload = self._fetch(body)
            self._cache.set(cache_key, payload)
        if self._raw_sink is not None:
            self._raw_sink.append({"query": body, "response": payload})
        return self.normalize(payload, origin=origin, destination=destination)

    def _fetch(self, body: dict[str, Any]) -> Any:
        token = self._require_token()
        response = request_with_backoff(
            self._client,
            "POST",
            f"{self._base_url}{_OFFER_REQUEST_PATH}",
            params={"return_offers": "true"},
            json=body,
            headers={
                "Authorization": f"Bearer {token}",
                "Duffel-Version": self._version,
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
        )
        if response.status_code in (200, 201):
            return response.json()
        if response.status_code == 422:
            # Unprocessable route (e.g. no offers): treat as empty, not a crash.
            return {"data": {"offers": []}}
        raise ProviderError(
            f"Duffel search failed ({response.status_code}): {response.text[:300]}"
        )

    # -- normalization ------------------------------------------------------

    @staticmethod
    def _parse_slice(slice_: dict[str, Any]) -> Leg | None:
        segments = slice_.get("segments", [])
        if not segments:
            return None
        first = segments[0]
        last = segments[-1]
        carrier = (first.get("marketing_carrier") or {}).get("iata_code", "")
        return Leg(
            origin=first["origin"]["iata_code"],
            destination=last["destination"]["iata_code"],
            depart=datetime.fromisoformat(first["departing_at"]),
            arrive=datetime.fromisoformat(last["arriving_at"]),
            duration_hours=parse_iso_duration_hours(slice_["duration"]),
            stops=len(segments) - 1,
            carrier=carrier,
        )

    def normalize(self, payload: Any, *, origin: str, destination: str) -> list[Offer]:
        """Map a Duffel offer-request payload to :class:`Offer`."""
        data = payload.get("data", {})
        offers: list[Offer] = []
        for item in data.get("offers", []):
            slices = item.get("slices", [])
            if len(slices) < 2:
                continue  # not a round trip
            outbound = self._parse_slice(slices[0])
            inbound = self._parse_slice(slices[1])
            if outbound is None or inbound is None:
                continue
            offers.append(
                Offer(
                    provider=self.name,
                    origin=origin,
                    destination=destination,
                    price=Decimal(str(item["total_amount"])),
                    currency=item["total_currency"],
                    outbound=outbound,
                    inbound=inbound,
                    raw_id=str(item.get("id")) if item.get("id") is not None else None,
                )
            )
        return offers

    def close(self) -> None:
        if self._owns_client:
            self._client.close()


__all__ = ["DuffelProvider"]
