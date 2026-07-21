"""Provider-agnostic domain models.

Every provider normalizes its raw JSON into these types so that all
downstream code (filtering, comparison, export) is identical regardless of
which backend produced the data. This is the single most important design
rule in the project: **constraints are enforced on ``Offer``, never on
provider-specific payloads.**
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, computed_field


class Leg(BaseModel):
    """One direction of a round trip (outbound or inbound).

    A leg may contain several segments (connections); ``stops`` is the number
    of connections (0 == non-stop). ``depart`` and ``arrive`` are the *local*
    times at the leg's origin and destination (used for time-of-day
    constraints). ``duration_hours`` is the true elapsed flight time supplied
    by the provider — never derived from ``arrive - depart``, because those
    local times sit in different timezones and their difference is wrong.
    """

    model_config = ConfigDict(frozen=True)

    origin: str = Field(description="IATA code the leg departs from.")
    destination: str = Field(description="IATA code the leg arrives at.")
    depart: datetime = Field(description="Local departure datetime.")
    arrive: datetime = Field(description="Local arrival datetime.")
    duration_hours: float = Field(
        gt=0, description="True elapsed leg duration in hours (provider-supplied)."
    )
    stops: int = Field(ge=0, description="Number of connections (0 = non-stop).")
    carrier: str = Field(description="Marketing carrier code for the leg.")


class Offer(BaseModel):
    """A normalized round-trip fare offered by a single provider.

    ``origin`` is the candidate origin airport this offer was searched from
    (a traveler may have several), which lets the comparison layer report
    *which* airport won for each traveler.
    """

    model_config = ConfigDict(frozen=True)

    provider: str = Field(
        description="Provider that produced this offer, e.g. 'amadeus'."
    )
    origin: str = Field(description="Origin IATA code searched for this offer.")
    destination: str = Field(description="Destination IATA code.")
    price: Decimal = Field(gt=0, description="Total round-trip price.")
    currency: str = Field(description="ISO-4217 currency of ``price``.")
    outbound: Leg = Field(description="Outbound (there) leg.")
    inbound: Leg = Field(description="Inbound (back) leg.")
    raw_id: str | None = Field(
        default=None, description="Provider's own offer id, if any."
    )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def max_stops(self) -> int:
        """Worst-case stops across both directions (used for filtering)."""
        return max(self.outbound.stops, self.inbound.stops)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def carrier(self) -> str:
        """Primary marketing carrier (outbound carrier)."""
        return self.outbound.carrier


class DateWindow(BaseModel):
    """A concrete depart/return date pair the whole group travels on."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    depart: date
    return_: date = Field(alias="return")

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"{self.depart.isoformat()} → {self.return_.isoformat()}"

    @property
    def key(self) -> str:
        """Stable identifier used in tables and cache keys."""
        return f"{self.depart.isoformat()}_{self.return_.isoformat()}"


class TravelerResult(BaseModel):
    """The outcome for one traveler within one date window."""

    traveler: str
    window: DateWindow
    best_offer: Offer | None = Field(
        default=None,
        description="Cheapest qualifying offer, or None if the traveler has no option.",
    )
    considered: int = Field(
        default=0, description="How many qualifying offers were compared."
    )

    @property
    def has_option(self) -> bool:
        return self.best_offer is not None


class WindowComparison(BaseModel):
    """Group-level comparison for a single date window."""

    window: DateWindow
    per_traveler: dict[str, TravelerResult]
    currency: str

    @property
    def missing(self) -> list[str]:
        """Travelers with zero qualifying options in this window."""
        return [name for name, r in self.per_traveler.items() if not r.has_option]

    @property
    def complete(self) -> bool:
        """True when every traveler has at least one qualifying option."""
        return not self.missing

    @property
    def group_total(self) -> Decimal | None:
        """Sum of each traveler's cheapest fare, or None if anyone is missing."""
        if not self.complete:
            return None
        total = Decimal(0)
        for result in self.per_traveler.values():
            assert result.best_offer is not None  # guaranteed by complete
            total += result.best_offer.price
        return total


class ComparisonReport(BaseModel):
    """Full report: every window compared and ranked by group total."""

    currency: str
    windows: list[WindowComparison]

    def ranked(self) -> list[WindowComparison]:
        """Windows ordered cheapest-first; incomplete windows sink to the end."""

        def sort_key(w: WindowComparison) -> tuple[int, Decimal, str]:
            total = w.group_total
            if total is None:
                return (1, Decimal(0), w.window.key)
            return (0, total, w.window.key)

        return sorted(self.windows, key=sort_key)

    def most_expensive_total(self) -> Decimal | None:
        """Highest complete group total (baseline for savings calculations)."""
        totals = [w.group_total for w in self.windows if w.group_total is not None]
        return max(totals) if totals else None
