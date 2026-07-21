"""Prove both providers normalize their raw JSON to identical Offer fields."""

from __future__ import annotations

from typing import Any

from converge_flights.config import Cabin, Constraints
from converge_flights.filters import cheapest, filter_offers
from converge_flights.providers.amadeus import AmadeusProvider
from converge_flights.providers.kiwi import KiwiProvider

LENIENT = Constraints(
    max_stops=2,
    max_duration_hours=20.0,
    depart_time_window=None,
    return_arrive_by=None,
    cabin=Cabin.ECONOMY,
    currency="USD",
)


def test_amadeus_normalizes_offer(amadeus_payload: dict[str, Any]) -> None:
    provider = AmadeusProvider(client=None)
    offers = provider.normalize(amadeus_payload, origin="JFK", destination="DEN")
    assert len(offers) == 2
    best = offers[0]
    assert best.provider == "amadeus"
    assert best.origin == "JFK"
    assert best.destination == "DEN"
    assert str(best.price) == "300.00"
    assert best.outbound.stops == 0
    assert best.outbound.duration_hours == 4.5
    assert best.inbound.duration_hours == 3.75
    assert best.carrier == "UA"


def test_kiwi_normalizes_offer(kiwi_payload: dict[str, Any]) -> None:
    provider = KiwiProvider(client=None)
    offers = provider.normalize(kiwi_payload, origin="JFK", destination="DEN")
    assert len(offers) == 2
    best = offers[0]
    assert best.provider == "kiwi"
    assert best.outbound.stops == 0
    assert best.outbound.duration_hours == 4.5
    assert best.inbound.duration_hours == 3.75
    assert best.carrier == "UA"


def test_both_providers_map_to_identical_offer_fields(
    amadeus_payload: dict[str, Any], kiwi_payload: dict[str, Any]
) -> None:
    """The canonical trip must normalize identically across providers.

    Everything except ``provider`` and ``raw_id`` (provider-specific
    metadata) must be equal, which is exactly what makes downstream filtering
    and comparison provider-agnostic.
    """
    amadeus = cheapest(
        filter_offers(
            AmadeusProvider(client=None).normalize(
                amadeus_payload, origin="JFK", destination="DEN"
            ),
            LENIENT,
        )
    )
    kiwi = cheapest(
        filter_offers(
            KiwiProvider(client=None).normalize(
                kiwi_payload, origin="JFK", destination="DEN"
            ),
            LENIENT,
        )
    )
    assert amadeus is not None
    assert kiwi is not None

    ignore = {"provider", "raw_id"}
    a = amadeus.model_dump(exclude=ignore)
    k = kiwi.model_dump(exclude=ignore)
    assert a == k
