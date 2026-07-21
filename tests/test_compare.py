"""Group comparison: cheapest-per-traveler, ranking, and missing options."""

from __future__ import annotations

from datetime import date

from converge_flights.compare import compare
from converge_flights.config import Config, Constraints
from converge_flights.models import DateWindow, Offer
from tests.conftest import make_leg, make_offer

WINDOW_A = DateWindow(depart=date(2025, 9, 11), return_=date(2025, 9, 14))
WINDOW_B = DateWindow(depart=date(2025, 9, 18), return_=date(2025, 9, 21))


class FakeProvider:
    """Return canned offers keyed by (origin, depart) for deterministic tests."""

    name = "fake"

    def __init__(self, table: dict[tuple[str, date], list[Offer]]) -> None:
        self._table = table

    def search(
        self,
        origin: str,
        destination: str,
        depart: date,
        return_: date,
        constraints: Constraints,
    ) -> list[Offer]:
        return self._table.get((origin, depart), [])


def _config() -> Config:
    return Config.model_validate(
        {
            "travelers": [
                {"name": "Alex", "origin_airports": ["LGA", "JFK"]},
                {"name": "Sam", "origin_airports": ["DTW"]},
            ],
            "destination": "DEN",
            "date_windows": {
                "explicit": [
                    {"depart": "2025-09-11", "return": "2025-09-14"},
                    {"depart": "2025-09-18", "return": "2025-09-21"},
                ]
            },
            "constraints": {"max_stops": 1, "max_duration_hours": 10, "currency": "USD"},
            "provider": "amadeus",
        }
    )


def test_cheapest_origin_and_group_total() -> None:
    table = {
        # Alex, window A: JFK cheaper than LGA -> JFK should win.
        ("LGA", WINDOW_A.depart): [make_offer(origin="LGA", price="350.00")],
        ("JFK", WINDOW_A.depart): [make_offer(origin="JFK", price="280.00")],
        ("DTW", WINDOW_A.depart): [make_offer(origin="DTW", price="200.00")],
        # Window B: pricier all around.
        ("LGA", WINDOW_B.depart): [make_offer(origin="LGA", price="500.00")],
        ("JFK", WINDOW_B.depart): [make_offer(origin="JFK", price="520.00")],
        ("DTW", WINDOW_B.depart): [make_offer(origin="DTW", price="410.00")],
    }
    report = compare([FakeProvider(table)], [WINDOW_A, WINDOW_B], _config())

    ranked = report.ranked()
    assert ranked[0].window == WINDOW_A  # cheaper window ranks first

    alex_a = ranked[0].per_traveler["Alex"].best_offer
    assert alex_a is not None
    assert alex_a.origin == "JFK"  # cheapest qualifying origin won
    assert str(ranked[0].group_total) == "480.00"  # 280 + 200


def test_missing_option_flagged() -> None:
    table = {
        ("LGA", WINDOW_A.depart): [make_offer(origin="LGA", price="350.00")],
        ("JFK", WINDOW_A.depart): [make_offer(origin="JFK", price="280.00")],
        # DTW has no offers in window A -> Sam is missing.
    }
    report = compare([FakeProvider(table)], [WINDOW_A], _config())
    window = report.windows[0]
    assert window.missing == ["Sam"]
    assert not window.complete
    assert window.group_total is None


def test_merge_across_providers_keeps_cheapest() -> None:
    """With two providers, the cheapest qualifying offer per traveler wins."""
    amadeus = FakeProvider(
        {
            ("JFK", WINDOW_A.depart): [
                make_offer(provider="amadeus", origin="JFK", price="300.00")
            ],
            ("DTW", WINDOW_A.depart): [
                make_offer(provider="amadeus", origin="DTW", price="250.00")
            ],
        }
    )
    kiwi = FakeProvider(
        {
            ("JFK", WINDOW_A.depart): [
                make_offer(provider="kiwi", origin="JFK", price="275.00")
            ],
            ("DTW", WINDOW_A.depart): [
                make_offer(provider="kiwi", origin="DTW", price="260.00")
            ],
        }
    )
    cfg = _config()
    report = compare([amadeus, kiwi], [WINDOW_A], cfg)
    window = report.windows[0]

    alex = window.per_traveler["Alex"].best_offer
    sam = window.per_traveler["Sam"].best_offer
    assert alex is not None and alex.provider == "kiwi"  # 275 < 300
    assert sam is not None and sam.provider == "amadeus"  # 250 < 260
    assert str(window.group_total) == "525.00"


def test_constraints_applied_uniformly_across_providers() -> None:
    """A cheap-but-nonqualifying offer is dropped regardless of provider."""
    bad_but_cheap = make_offer(
        provider="kiwi", origin="JFK", price="100.00", outbound=make_leg(stops=3)
    )
    good = make_offer(provider="amadeus", origin="JFK", price="300.00")
    providers = [
        FakeProvider({("JFK", WINDOW_A.depart): [bad_but_cheap]}),
        FakeProvider(
            {
                ("JFK", WINDOW_A.depart): [good],
                ("DTW", WINDOW_A.depart): [make_offer(origin="DTW", price="200.00")],
            }
        ),
    ]
    report = compare(providers, [WINDOW_A], _config())
    alex = report.windows[0].per_traveler["Alex"].best_offer
    assert alex is not None
    assert str(alex.price) == "300.00"  # the 3-stop $100 offer was filtered out
