"""Flight-price providers and the factory that builds them from config."""

from __future__ import annotations

import httpx

from converge_flights.cache import QueryCache
from converge_flights.config import Config, Provider
from converge_flights.providers.amadeus import AmadeusProvider
from converge_flights.providers.base import FlightProvider
from converge_flights.providers.kiwi import KiwiProvider


def build_providers(
    config: Config,
    *,
    client: httpx.Client | None = None,
    cache: QueryCache | None = None,
    raw_sinks: dict[str, list[dict[str, object]]] | None = None,
) -> list[FlightProvider]:
    """Instantiate the provider(s) selected in ``config``.

    Raises a clear error (from within each provider) if a required API key is
    missing. Returns a list because ``provider: both`` runs two providers and
    merges results downstream. When ``raw_sinks`` is supplied, each provider
    appends its raw request/response pairs to ``raw_sinks[provider_name]`` for
    the optional JSON dump.
    """
    cache = cache or QueryCache(config.cache)
    selected = config.provider
    providers: list[FlightProvider] = []
    if selected in (Provider.AMADEUS, Provider.BOTH):
        sink = raw_sinks.setdefault("amadeus", []) if raw_sinks is not None else None
        providers.append(AmadeusProvider(client=client, cache=cache, raw_sink=sink))
    if selected in (Provider.KIWI, Provider.BOTH):
        sink = raw_sinks.setdefault("kiwi", []) if raw_sinks is not None else None
        providers.append(KiwiProvider(client=client, cache=cache, raw_sink=sink))
    return providers


__all__ = [
    "AmadeusProvider",
    "FlightProvider",
    "KiwiProvider",
    "build_providers",
]
