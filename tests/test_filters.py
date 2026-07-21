"""Constraint enforcement on the normalized Offer layer."""

from __future__ import annotations

from datetime import time

from converge_flights.config import Constraints, TimeWindow
from converge_flights.filters import cheapest, filter_offers, qualifies, rejection_reason
from tests.conftest import make_leg, make_offer


def test_stops_over_cap_rejected() -> None:
    offer = make_offer(outbound=make_leg(stops=2))
    c = Constraints(max_stops=1)
    assert not qualifies(offer, c)
    assert "stops" in (rejection_reason(offer, c) or "")


def test_duration_over_cap_rejected() -> None:
    offer = make_offer(outbound=make_leg(duration_hours=12.0))
    assert not qualifies(offer, Constraints(max_duration_hours=10.0))


def test_depart_time_window_enforced() -> None:
    # Departs 09:00, window is 12:00-18:00 -> rejected.
    early = make_offer(
        outbound=make_leg(depart="2025-09-11T09:00:00", arrive="2025-09-11T11:30:00")
    )
    c = Constraints(depart_time_window=TimeWindow(start=time(12, 0), end=time(18, 0)))
    assert not qualifies(early, c)

    ok = make_offer(
        outbound=make_leg(depart="2025-09-11T14:30:00", arrive="2025-09-11T17:00:00")
    )
    assert qualifies(ok, c)


def test_return_arrive_by_enforced() -> None:
    late = make_offer(
        inbound=make_leg(
            origin="DEN",
            destination="JFK",
            depart="2025-09-14T20:00:00",
            arrive="2025-09-14T23:30:00",
        )
    )
    c = Constraints(return_arrive_by=time(21, 0))
    assert not qualifies(late, c)


def test_currency_mismatch_rejected() -> None:
    offer = make_offer(currency="EUR")
    assert not qualifies(offer, Constraints(currency="USD"))


def test_filter_and_cheapest() -> None:
    cheap = make_offer(price="200.00")
    mid = make_offer(price="300.00")
    bad = make_offer(price="150.00", outbound=make_leg(stops=3))
    kept = filter_offers([cheap, mid, bad], Constraints(max_stops=1))
    assert bad not in kept
    best = cheapest(kept)
    assert best is not None
    assert str(best.price) == "200.00"


def test_cheapest_empty_is_none() -> None:
    assert cheapest([]) is None
