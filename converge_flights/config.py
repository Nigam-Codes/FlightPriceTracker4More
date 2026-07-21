"""Typed configuration loaded from ``config.yaml``.

The config drives everything: who is travelling, from where, to where, on
which candidate date windows, and under which shared constraints. Date
windows may be given explicitly or generated from a recurring weekday rule.
"""

from __future__ import annotations

import enum
from datetime import date, time
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class Provider(str, enum.Enum):
    """Selectable flight-price backend(s)."""

    AMADEUS = "amadeus"
    KIWI = "kiwi"
    BOTH = "both"


class Cabin(str, enum.Enum):
    """Cabin class, normalized across providers."""

    ECONOMY = "economy"
    PREMIUM_ECONOMY = "premium_economy"
    BUSINESS = "business"
    FIRST = "first"


class TravelerConfig(BaseModel):
    """One member of the group and their candidate home airports."""

    model_config = ConfigDict(frozen=True)

    name: str
    origin_airports: list[str] = Field(
        min_length=1,
        description="Candidate origin IATA codes; the cheapest qualifying one wins.",
    )


class ExplicitWindow(BaseModel):
    """A single depart/return date pair given verbatim in config."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    depart: date
    return_: date = Field(alias="return")


_WEEKDAYS: dict[str, int] = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}


class RecurringRule(BaseModel):
    """Generate date windows from a weekly rule.

    Example: "every Thursday to the following Sunday from 2025-09-10 to
    2025-12-31" becomes ``depart_weekday: thursday``,
    ``return_weekday: sunday``, ``start`` and ``end``.
    """

    model_config = ConfigDict(frozen=True)

    depart_weekday: str
    return_weekday: str
    start: date
    end: date

    @model_validator(mode="after")
    def _validate(self) -> RecurringRule:
        if self.depart_weekday.lower() not in _WEEKDAYS:
            raise ValueError(f"Unknown depart_weekday: {self.depart_weekday!r}")
        if self.return_weekday.lower() not in _WEEKDAYS:
            raise ValueError(f"Unknown return_weekday: {self.return_weekday!r}")
        if self.end < self.start:
            raise ValueError("date_windows.recurring.end is before start")
        return self

    @property
    def depart_dow(self) -> int:
        return _WEEKDAYS[self.depart_weekday.lower()]

    @property
    def return_dow(self) -> int:
        return _WEEKDAYS[self.return_weekday.lower()]


class DateWindowsConfig(BaseModel):
    """Either an explicit list of windows or a recurring rule (exactly one)."""

    model_config = ConfigDict(frozen=True)

    explicit: list[ExplicitWindow] | None = None
    recurring: RecurringRule | None = None

    @model_validator(mode="after")
    def _exactly_one(self) -> DateWindowsConfig:
        if bool(self.explicit) == bool(self.recurring):
            raise ValueError(
                "date_windows must set exactly one of 'explicit' or 'recurring'"
            )
        return self


class TimeWindow(BaseModel):
    """A local time-of-day range, e.g. depart between 12:00 and 18:00."""

    model_config = ConfigDict(frozen=True)

    start: time
    end: time

    @model_validator(mode="after")
    def _ordered(self) -> TimeWindow:
        if self.end <= self.start:
            raise ValueError("time window end must be after start")
        return self


class Constraints(BaseModel):
    """Shared filtering rules applied identically to every normalized offer."""

    model_config = ConfigDict(frozen=True)

    max_stops: int = Field(default=1, ge=0)
    max_duration_hours: float = Field(default=10.0, gt=0, description="Per direction.")
    depart_time_window: TimeWindow | None = Field(
        default=None, description="Local outbound departure must fall in this range."
    )
    return_arrive_by: time | None = Field(
        default=None, description="Local inbound arrival must be no later than this time."
    )
    cabin: Cabin = Cabin.ECONOMY
    currency: str = Field(default="USD", min_length=3, max_length=3)


class CacheConfig(BaseModel):
    """On-disk cache so re-runs don't burn free-tier quota."""

    model_config = ConfigDict(frozen=True)

    enabled: bool = True
    directory: Path = Path(".cache/converge_flights")
    ttl_hours: float = Field(default=24.0, gt=0)


class Config(BaseModel):
    """Root configuration object parsed from ``config.yaml``."""

    model_config = ConfigDict(frozen=True)

    travelers: list[TravelerConfig] = Field(min_length=1)
    destination: str
    date_windows: DateWindowsConfig
    constraints: Constraints = Constraints()
    provider: Provider = Provider.AMADEUS
    cache: CacheConfig = CacheConfig()
    raw_dump: bool = Field(
        default=False, description="If true, dump raw provider JSON alongside output."
    )

    @model_validator(mode="after")
    def _unique_names(self) -> Config:
        names = [t.name for t in self.travelers]
        if len(names) != len(set(names)):
            raise ValueError("traveler names must be unique")
        return self

    @classmethod
    def from_yaml(cls, path: str | Path) -> Config:
        """Load and validate configuration from a YAML file."""
        text = Path(path).read_text(encoding="utf-8")
        data = yaml.safe_load(text)
        if not isinstance(data, dict):
            raise ValueError(f"Config file {path} did not parse to a mapping.")
        return cls.model_validate(data)


# Amadeus expects these cabin codes; Kiwi uses lowercase single letters.
AMADEUS_CABIN: dict[Cabin, str] = {
    Cabin.ECONOMY: "ECONOMY",
    Cabin.PREMIUM_ECONOMY: "PREMIUM_ECONOMY",
    Cabin.BUSINESS: "BUSINESS",
    Cabin.FIRST: "FIRST",
}

KIWI_CABIN: dict[Cabin, str] = {
    Cabin.ECONOMY: "M",
    Cabin.PREMIUM_ECONOMY: "W",
    Cabin.BUSINESS: "C",
    Cabin.FIRST: "F",
}

__all__ = [
    "AMADEUS_CABIN",
    "KIWI_CABIN",
    "Cabin",
    "CacheConfig",
    "Config",
    "Constraints",
    "DateWindowsConfig",
    "ExplicitWindow",
    "Provider",
    "RecurringRule",
    "TimeWindow",
    "TravelerConfig",
]
