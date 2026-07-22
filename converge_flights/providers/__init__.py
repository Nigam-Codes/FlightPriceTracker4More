"""Flight-price providers and the factory that builds them from config."""

from __future__ import annotations

import httpx

from converge_flights.cache import QueryCache
from converge_flights.config import Config
from converge_flights.providers.amadeus import AmadeusProvider
from converge_flights.providers.base import FlightProvider
from converge_flights.providers.duffel import DuffelProvider
from converge_flights.providers.kiwi import KiwiProvider


def _make_provider(
    name: str,
    *,
    client: httpx.Client | None,
    cache: QueryCache,
    raw_sink: list[dict[str, object]] | None,
) -> FlightProvider:
    """Instantiate a single provider by its normalized name."""
    if name == "amadeus":
        return AmadeusProvider(client=client, cache=cache, raw_sink=raw_sink)
    if name == "kiwi":
        return KiwiProvider(client=client, cache=cache, raw_sink=raw_sink)
    if name == "duffel":
        return DuffelProvider(client=client, cache=cache, raw_sink=raw_sink)
    raise ValueError(f"Unknown provider: {name!r}")


def build_providers(
    config: Config,
    *,
    client: httpx.Client | None = None,
    cache: QueryCache | None = None,
    raw_sinks: dict[str, list[dict[str, object]]] | None = None,
) -> list[FlightProvider]:
    """Instantiate every provider selected in ``config``.

    Driven by :attr:`Config.selected_providers`, so it scales to any subset
    (``amadeus``, ``kiwi``, ``duffel``, the ``both`` alias, or an explicit
    list). Each provider raises a clear error if its API key is missing.
    Results from multiple providers are merged downstream, keeping the cheapest
    qualifying offer per traveler. When ``raw_sinks`` is supplied, each provider
    appends its raw request/response pairs to ``raw_sinks[provider_name]``.
    """
    cache = cache or QueryCache(config.cache)
    providers: list[FlightProvider] = []
    for name in config.selected_providers:
        sink = raw_sinks.setdefault(name, []) if raw_sinks is not None else None
        providers.append(_make_provider(name, client=client, cache=cache, raw_sink=sink))
    return providers


__all__ = [
    "AmadeusProvider",
    "DuffelProvider",
    "FlightProvider",
    "KiwiProvider",
    "build_providers",
]
