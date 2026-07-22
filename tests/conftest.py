"""Shared test fixtures and builders (all offline)."""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from converge_flights.models import Leg, Offer

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def amadeus_payload() -> dict[str, Any]:
    """Recorded Amadeus Flight Offers Search response."""
    return json.loads((FIXTURES / "amadeus_response.json").read_text())


@pytest.fixture
def kiwi_payload() -> dict[str, Any]:
    """Recorded Kiwi/Tequila Search response."""
    return json.loads((FIXTURES / "kiwi_response.json").read_text())


@pytest.fixture
def duffel_payload() -> dict[str, Any]:
    """Recorded Duffel offer-request response (with embedded offers)."""
    return json.loads((FIXTURES / "duffel_offer_request.json").read_text())


def make_leg(
    *,
    origin: str = "JFK",
    destination: str = "DEN",
    depart: str = "2025-09-11T14:30:00",
    arrive: str = "2025-09-11T17:00:00",
    duration_hours: float = 4.5,
    stops: int = 0,
    carrier: str = "UA",
) -> Leg:
    """Construct a :class:`Leg` with sensible defaults for tests."""
    return Leg(
        origin=origin,
        destination=destination,
        depart=datetime.fromisoformat(depart),
        arrive=datetime.fromisoformat(arrive),
        duration_hours=duration_hours,
        stops=stops,
        carrier=carrier,
    )


def make_offer(
    *,
    provider: str = "amadeus",
    origin: str = "JFK",
    destination: str = "DEN",
    price: str = "300.00",
    currency: str = "USD",
    outbound: Leg | None = None,
    inbound: Leg | None = None,
) -> Offer:
    """Construct an :class:`Offer` with sensible defaults for tests."""
    return Offer(
        provider=provider,
        origin=origin,
        destination=destination,
        price=Decimal(price),
        currency=currency,
        outbound=outbound or make_leg(),
        inbound=inbound
        or make_leg(
            origin="DEN",
            destination="JFK",
            depart="2025-09-14T18:00:00",
            arrive="2025-09-14T20:30:00",
            duration_hours=3.75,
        ),
    )
