"""The ``FlightProvider`` protocol and shared HTTP helpers.

Every backend implements :class:`FlightProvider` so that the rest of the code
is provider-agnostic. Providers are responsible only for *fetching* and
*normalizing* into :class:`~converge_flights.models.Offer`; they must not
apply group constraints — that happens in :mod:`converge_flights.filters`.
"""

from __future__ import annotations

import time
from datetime import date
from typing import Any, Protocol, runtime_checkable

import httpx

from converge_flights.config import Constraints
from converge_flights.models import Offer


class ProviderError(RuntimeError):
    """Raised for configuration or unrecoverable API errors."""


class MissingCredentialsError(ProviderError):
    """Raised when a selected provider's API key/secret is not configured."""


@runtime_checkable
class FlightProvider(Protocol):
    """Fetch and normalize round-trip fares for a single origin.

    Implementations return a list of normalized :class:`Offer` objects for a
    single ``origin`` → ``destination`` round trip on the given dates. An
    empty list means "no offers found" (not an error).
    """

    name: str

    def search(
        self,
        origin: str,
        destination: str,
        depart: date,
        return_: date,
        constraints: Constraints,
    ) -> list[Offer]:
        """Return normalized offers for one round trip. Never raises on empty."""
        ...


def request_with_backoff(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    max_retries: int = 4,
    base_delay: float = 1.0,
    sleep: Any = time.sleep,
    **kwargs: Any,
) -> httpx.Response:
    """Perform an HTTP request, retrying on 429/5xx with exponential backoff.

    Honors a ``Retry-After`` header when present. Both providers document
    strict per-second/per-month rate limits (see their modules), so every
    network call funnels through here.
    """
    last_exc: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            response = client.request(method, url, **kwargs)
        except httpx.TransportError as exc:  # network hiccup
            last_exc = exc
            if attempt == max_retries:
                raise
            sleep(base_delay * (2**attempt))
            continue

        if response.status_code == 429 or response.status_code >= 500:
            if attempt == max_retries:
                return response
            retry_after = response.headers.get("Retry-After")
            delay = base_delay * (2**attempt)
            if retry_after is not None:
                try:
                    delay = max(delay, float(retry_after))
                except ValueError:
                    pass
            sleep(delay)
            continue

        return response

    # Unreachable in practice, but keeps type-checkers satisfied.
    if last_exc is not None:  # pragma: no cover
        raise last_exc
    raise ProviderError("request_with_backoff exhausted retries")  # pragma: no cover


__all__ = [
    "FlightProvider",
    "MissingCredentialsError",
    "ProviderError",
    "request_with_backoff",
]
