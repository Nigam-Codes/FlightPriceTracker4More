"""Search orchestration and group comparison.

For each traveler × date window, query every candidate origin through the
selected provider(s), filter by shared constraints on the normalized
:class:`Offer` layer, and keep the single cheapest qualifying round trip.
Group totals per window are then ranked cheapest-first.
"""

from __future__ import annotations

from converge_flights.config import Config
from converge_flights.filters import cheapest, filter_offers
from converge_flights.models import (
    ComparisonReport,
    DateWindow,
    Offer,
    TravelerResult,
    WindowComparison,
)
from converge_flights.providers.base import FlightProvider


def collect_offers(
    providers: list[FlightProvider],
    origins: list[str],
    destination: str,
    window: DateWindow,
    config: Config,
) -> list[Offer]:
    """Gather qualifying offers for one traveler across origins/providers.

    Every ``(provider, origin)`` combination is searched; results are pooled
    and filtered once, on the normalized layer, so a Kiwi offer and an Amadeus
    offer are judged by identical rules. When ``provider: both`` is set this is
    how the cheapest qualifying offer per traveler is kept *across* providers.
    """
    pooled: list[Offer] = []
    for provider in providers:
        for origin in origins:
            offers = provider.search(
                origin,
                destination,
                window.depart,
                window.return_,
                config.constraints,
            )
            pooled.extend(offers)
    return filter_offers(pooled, config.constraints)


def compare(
    providers: list[FlightProvider],
    windows: list[DateWindow],
    config: Config,
) -> ComparisonReport:
    """Build a full :class:`ComparisonReport` for every window."""
    comparisons: list[WindowComparison] = []
    for window in windows:
        per_traveler: dict[str, TravelerResult] = {}
        for traveler in config.travelers:
            qualifying = collect_offers(
                providers,
                traveler.origin_airports,
                config.destination,
                window,
                config,
            )
            best = cheapest(qualifying)
            per_traveler[traveler.name] = TravelerResult(
                traveler=traveler.name,
                window=window,
                best_offer=best,
                considered=len(qualifying),
            )
        comparisons.append(
            WindowComparison(
                window=window,
                per_traveler=per_traveler,
                currency=config.constraints.currency,
            )
        )
    return ComparisonReport(currency=config.constraints.currency, windows=comparisons)


__all__ = ["collect_offers", "compare"]
