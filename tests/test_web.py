"""Web app tests — offline, driven by a stub provider (no network, no quota)."""

from __future__ import annotations

import re
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

import converge_flights.web as web
from converge_flights.config import Constraints
from converge_flights.models import Offer
from converge_flights.providers.base import MissingCredentialsError
from tests.conftest import make_offer

PRICES = {"LGA": "330.00", "JFK": "288.00", "EWR": "351.00", "DTW": "205.00"}


class StubProvider:
    """Returns deterministic fares so results are assertable."""

    name = "serpapi"

    def search(
        self,
        origin: str,
        destination: str,
        depart: date,
        return_: date,
        constraints: Constraints,
    ) -> list[Offer]:
        bump = Decimal(60) if depart.day % 2 == 0 else Decimal(0)
        price = str(Decimal(PRICES[origin]) + bump)
        return [make_offer(provider="serpapi", origin=origin, price=price)]


def _future_windows() -> str:
    today = date.today()
    first = today + timedelta(days=((3 - today.weekday()) % 7) or 7)
    second = first + timedelta(weeks=1)
    return (
        f"{first} to {first + timedelta(days=3)}\n"
        f"{second} to {second + timedelta(days=3)}"
    )


def _form(**overrides: Any) -> dict[str, Any]:
    data = {
        "travelers": "Alex: LGA, JFK, EWR\nSam: DTW",
        "destination": "DEN",
        "windows": _future_windows(),
        "provider": "serpapi",
        "max_stops": 1,
        "max_duration_hours": 10.0,
        "currency": "USD",
        "cabin": "economy",
    }
    data.update(overrides)
    return data


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(web, "build_providers", lambda cfg, **kw: [StubProvider()])
    return TestClient(web.app)


def test_index_renders_form(client: TestClient) -> None:
    resp = client.get("/")
    assert resp.status_code == 200
    for field in ("travelers", "destination", "windows", "provider"):
        assert f'name="{field}"' in resp.text


def test_index_defaults_are_future_dates() -> None:
    windows = web.default_windows()
    for line in windows.splitlines():
        depart = date.fromisoformat(line.split(" to ")[0])
        assert depart > date.today()


def test_healthz() -> None:
    assert TestClient(web.app).get("/healthz").json() == {"status": "ok"}


def test_search_ranks_and_picks_cheapest_origin(client: TestClient) -> None:
    resp = client.post("/search", data=_form())
    assert resp.status_code == 200
    # Cheapest NYC airport (JFK at 288) beats LGA/EWR; DTW is Sam's only option.
    assert "serpapi/JFK" in resp.text
    assert "serpapi/LGA" not in resp.text
    totals = [float(t) for t in re.findall(r"([\d.]+) USD", resp.text)]
    assert totals == sorted(totals), "windows must be ranked cheapest-first"


def test_search_offers_xlsx_download(client: TestClient) -> None:
    resp = client.post("/search", data=_form())
    token = re.search(r"/download/([a-f0-9]+)", resp.text)
    assert token is not None
    dl = client.get(f"/download/{token.group(1)}")
    assert dl.status_code == 200
    assert dl.content[:2] == b"PK"  # xlsx is a zip archive
    assert "spreadsheetml" in dl.headers["content-type"]


def test_download_rejects_unknown_and_traversal_tokens(client: TestClient) -> None:
    assert client.get("/download/deadbeef").status_code == 404
    assert client.get("/download/..%2F..%2Fetc%2Fpasswd").status_code == 404


def test_past_dates_are_flagged(client: TestClient) -> None:
    resp = client.post("/search", data=_form(windows="2020-01-02 to 2020-01-05"))
    assert resp.status_code == 200
    assert "in the past" in resp.text


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("travelers", "no-colon-here"),
        ("travelers", ""),
        ("windows", "2026-08-13"),
        ("windows", "not-a-date to also-not"),
        ("windows", "2026-08-16 to 2026-08-13"),  # return before departure
    ],
)
def test_invalid_input_returns_readable_error(
    client: TestClient, field: str, value: str
) -> None:
    resp = client.post("/search", data=_form(**{field: value}))
    assert resp.status_code == 400
    assert "Couldn&#39;t run that search" in resp.text or "Couldn't run" in resp.text


def test_missing_credentials_shown_in_page(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(cfg: Any, **kw: Any) -> Any:
        raise MissingCredentialsError("SERPAPI_API_KEY is not set.")

    monkeypatch.setattr(web, "build_providers", boom)
    resp = TestClient(web.app).post("/search", data=_form())
    assert resp.status_code == 400
    assert "Missing credentials" in resp.text
    assert "SERPAPI_API_KEY" in resp.text


def test_network_error_shown_in_page(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(web, "build_providers", lambda cfg, **kw: [StubProvider()])

    def boom(*a: Any, **k: Any) -> Any:
        raise httpx.ProxyError("403 Forbidden")

    monkeypatch.setattr(web, "compare", boom)
    resp = TestClient(web.app).post("/search", data=_form())
    assert resp.status_code == 400
    assert "Network error" in resp.text
