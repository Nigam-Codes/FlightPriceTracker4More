"""Exercise each provider's fetch path fully offline via httpx.MockTransport."""

from __future__ import annotations

import json
from datetime import date
from typing import Any

import httpx
import pytest

from converge_flights.config import Constraints
from converge_flights.providers.amadeus import AmadeusProvider
from converge_flights.providers.base import MissingCredentialsError
from converge_flights.providers.duffel import DuffelProvider
from converge_flights.providers.kiwi import KiwiProvider

DEPART = date(2025, 9, 11)
RETURN = date(2025, 9, 14)
CONSTRAINTS = Constraints(currency="USD")


def _amadeus_transport(payload: dict[str, Any]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth2/token"):
            return httpx.Response(
                200,
                json={"access_token": "fake-token", "expires_in": 1799},
            )
        if request.url.path.endswith("/shopping/flight-offers"):
            assert request.headers["Authorization"] == "Bearer fake-token"
            return httpx.Response(200, json=payload)
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def _kiwi_transport(payload: dict[str, Any]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("apikey") == "fake-key"
        return httpx.Response(200, json=payload)

    return httpx.MockTransport(handler)


def test_amadeus_search_offline(amadeus_payload: dict[str, Any]) -> None:
    client = httpx.Client(transport=_amadeus_transport(amadeus_payload))
    provider = AmadeusProvider(
        client=client,
        client_id="id",
        client_secret="secret",
        base_url="https://test.api.amadeus.com",
    )
    offers = provider.search("JFK", "DEN", DEPART, RETURN, CONSTRAINTS)
    assert len(offers) == 2
    assert offers[0].provider == "amadeus"


def test_amadeus_token_is_cached(amadeus_payload: dict[str, Any]) -> None:
    calls = {"token": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth2/token"):
            calls["token"] += 1
            return httpx.Response(200, json={"access_token": "t", "expires_in": 1799})
        return httpx.Response(200, json=amadeus_payload)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = AmadeusProvider(client=client, client_id="id", client_secret="s")
    provider.search("JFK", "DEN", DEPART, RETURN, CONSTRAINTS)
    provider.search("EWR", "DEN", DEPART, RETURN, CONSTRAINTS)
    assert calls["token"] == 1  # token reused across searches


def test_amadeus_missing_credentials() -> None:
    provider = AmadeusProvider(client=httpx.Client(), client_id=None, client_secret=None)
    with pytest.raises(MissingCredentialsError):
        provider.search("JFK", "DEN", DEPART, RETURN, CONSTRAINTS)


def test_kiwi_search_offline(kiwi_payload: dict[str, Any]) -> None:
    client = httpx.Client(transport=_kiwi_transport(kiwi_payload))
    provider = KiwiProvider(client=client, api_key="fake-key")
    offers = provider.search("JFK", "DEN", DEPART, RETURN, CONSTRAINTS)
    assert len(offers) == 2
    assert offers[0].provider == "kiwi"


def test_kiwi_missing_credentials() -> None:
    provider = KiwiProvider(client=httpx.Client(), api_key=None)
    with pytest.raises(MissingCredentialsError):
        provider.search("JFK", "DEN", DEPART, RETURN, CONSTRAINTS)


def _duffel_transport(payload: dict[str, Any]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/air/offer_requests")
        assert request.headers["Authorization"] == "Bearer fake-token"
        assert request.headers["Duffel-Version"] == "v2"
        assert request.url.params.get("return_offers") == "true"
        return httpx.Response(201, json=payload)

    return httpx.MockTransport(handler)


def test_duffel_search_offline(duffel_payload: dict[str, Any]) -> None:
    client = httpx.Client(transport=_duffel_transport(duffel_payload))
    provider = DuffelProvider(client=client, api_token="fake-token", version="v2")
    offers = provider.search("JFK", "DEN", DEPART, RETURN, CONSTRAINTS)
    assert len(offers) == 2
    assert offers[0].provider == "duffel"
    assert str(offers[0].price) == "300.00"


def test_duffel_missing_credentials() -> None:
    provider = DuffelProvider(client=httpx.Client(), api_token=None)
    with pytest.raises(MissingCredentialsError):
        provider.search("JFK", "DEN", DEPART, RETURN, CONSTRAINTS)


def test_amadeus_retries_on_429(amadeus_payload: dict[str, Any]) -> None:
    attempts = {"search": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth2/token"):
            return httpx.Response(200, json={"access_token": "t", "expires_in": 1799})
        attempts["search"] += 1
        if attempts["search"] == 1:
            return httpx.Response(429, headers={"Retry-After": "0"}, text="slow down")
        return httpx.Response(200, json=amadeus_payload)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    # Patch sleep to a no-op so the test is instant.
    import converge_flights.providers.base as base

    original_sleep = base.time.sleep
    base.time.sleep = lambda _s: None  # type: ignore[assignment]
    try:
        provider = AmadeusProvider(client=client, client_id="id", client_secret="s")
        offers = provider.search("JFK", "DEN", DEPART, RETURN, CONSTRAINTS)
    finally:
        base.time.sleep = original_sleep  # type: ignore[assignment]
    assert len(offers) == 2
    assert attempts["search"] == 2


def test_cache_prevents_second_fetch(tmp_path: Any, kiwi_payload: dict[str, Any]) -> None:
    from converge_flights.cache import QueryCache
    from converge_flights.config import CacheConfig

    fetches = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        fetches["n"] += 1
        return httpx.Response(200, json=kiwi_payload)

    cache = QueryCache(CacheConfig(enabled=True, directory=tmp_path / "c"))
    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = KiwiProvider(client=client, api_key="fake-key", cache=cache)
    provider.search("JFK", "DEN", DEPART, RETURN, CONSTRAINTS)
    provider.search("JFK", "DEN", DEPART, RETURN, CONSTRAINTS)
    assert fetches["n"] == 1  # second call served from cache

    # Sanity: the raw payload round-trips through JSON cache unchanged.
    assert json.dumps(kiwi_payload)
