"""Constraint enforcement on normalized offers.

Amadeus and Kiwi model stops and durations very differently in their raw
payloads, so filtering **must** happen here, on the shared :class:`Offer`
type, and never inside a provider. That guarantees identical qualification
logic regardless of where an offer came from.
"""

from __future__ import annotations

from converge_flights.config import Constraints
from converge_flights.models import Offer


def _reason(offer: Offer, constraints: Constraints) -> str | None:
    """Return why an offer fails, or ``None`` if it qualifies."""
    if offer.currency.upper() != constraints.currency.upper():
        return f"currency {offer.currency} != required {constraints.currency}"

    if offer.outbound.stops > constraints.max_stops:
        return f"outbound stops {offer.outbound.stops} > {constraints.max_stops}"
    if offer.inbound.stops > constraints.max_stops:
        return f"inbound stops {offer.inbound.stops} > {constraints.max_stops}"

    if offer.outbound.duration_hours > constraints.max_duration_hours:
        return (
            f"outbound {offer.outbound.duration_hours:.1f}h "
            f"> {constraints.max_duration_hours}h"
        )
    if offer.inbound.duration_hours > constraints.max_duration_hours:
        return (
            f"inbound {offer.inbound.duration_hours:.1f}h "
            f"> {constraints.max_duration_hours}h"
        )

    if constraints.depart_time_window is not None:
        depart_t = offer.outbound.depart.time()
        win = constraints.depart_time_window
        if not (win.start <= depart_t <= win.end):
            return (
                f"outbound departs {depart_t.strftime('%H:%M')} outside "
                f"{win.start.strftime('%H:%M')}-{win.end.strftime('%H:%M')}"
            )

    if constraints.return_arrive_by is not None:
        arrive_t = offer.inbound.arrive.time()
        if arrive_t > constraints.return_arrive_by:
            return (
                f"inbound arrives {arrive_t.strftime('%H:%M')} after "
                f"{constraints.return_arrive_by.strftime('%H:%M')}"
            )

    return None


def qualifies(offer: Offer, constraints: Constraints) -> bool:
    """True when ``offer`` satisfies every shared constraint."""
    return _reason(offer, constraints) is None


def rejection_reason(offer: Offer, constraints: Constraints) -> str | None:
    """Human-readable reason an offer was filtered out (or ``None``)."""
    return _reason(offer, constraints)


def filter_offers(offers: list[Offer], constraints: Constraints) -> list[Offer]:
    """Return only the offers that satisfy ``constraints``."""
    return [o for o in offers if qualifies(o, constraints)]


def cheapest(offers: list[Offer]) -> Offer | None:
    """Return the lowest-priced offer, or ``None`` for an empty list."""
    if not offers:
        return None
    return min(offers, key=lambda o: o.price)


__all__ = [
    "cheapest",
    "filter_offers",
    "qualifies",
    "rejection_reason",
]
