"""Config parsing: provider selection (scalar, list, and the ``both`` alias)."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from converge_flights.config import Config

_BASE: dict[str, Any] = {
    "travelers": [{"name": "Alex", "origin_airports": ["JFK"]}],
    "destination": "DEN",
    "date_windows": {"explicit": [{"depart": "2025-09-11", "return": "2025-09-14"}]},
}


def _config(provider: Any) -> Config:
    return Config.model_validate({**_BASE, "provider": provider})


def test_scalar_provider() -> None:
    assert _config("amadeus").selected_providers == ["amadeus"]
    assert _config("kiwi").selected_providers == ["kiwi"]
    assert _config("duffel").selected_providers == ["duffel"]
    assert _config("serpapi").selected_providers == ["serpapi"]


def test_both_alias_expands() -> None:
    assert _config("both").selected_providers == ["amadeus", "kiwi"]


def test_list_provider() -> None:
    assert _config(["serpapi", "duffel"]).selected_providers == ["serpapi", "duffel"]
    assert _config(["amadeus", "duffel"]).selected_providers == ["amadeus", "duffel"]
    assert _config(["duffel", "kiwi", "amadeus"]).selected_providers == [
        "duffel",
        "kiwi",
        "amadeus",
    ]


def test_list_with_alias_dedupes() -> None:
    # 'both' -> amadeus, kiwi; then duffel appended; no duplicates.
    assert _config(["both", "duffel"]).selected_providers == [
        "amadeus",
        "kiwi",
        "duffel",
    ]
    assert _config(["amadeus", "amadeus"]).selected_providers == ["amadeus"]


def test_default_is_amadeus() -> None:
    assert Config.model_validate(_BASE).selected_providers == ["amadeus"]


def test_empty_list_rejected() -> None:
    with pytest.raises(ValidationError):
        _config([])


def test_unknown_provider_rejected() -> None:
    with pytest.raises(ValidationError):
        _config("expedia")
