"""FastAPI web app for converge-flights.

A browser front end over the same engine the CLI uses: a form describes the
group and the trip, the **server** runs the provider search, and the ranked
comparison is rendered as HTML with the ``.xlsx`` workbook offered as a
download.

Why server-side: provider API keys (``SERPAPI_API_KEY`` etc.) are read from the
server's environment and never reach the browser. The page posts a plain form;
no credentials are ever embedded in client JavaScript.

Run locally::

    uvicorn converge_flights.web:app --reload

Everything below reuses the library: :class:`~converge_flights.config.Config`,
:func:`~converge_flights.providers.build_providers`,
:func:`~converge_flights.compare.compare` and
:func:`~converge_flights.export.export_report`.
"""

from __future__ import annotations

import tempfile
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, Form, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.templating import Jinja2Templates

from converge_flights.cache import QueryCache
from converge_flights.compare import compare
from converge_flights.config import Config
from converge_flights.dates import build_windows
from converge_flights.export import export_report
from converge_flights.models import ComparisonReport
from converge_flights.providers import build_providers
from converge_flights.providers.base import MissingCredentialsError, ProviderError

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

app = FastAPI(title="converge-flights", docs_url="/api/docs")

# Generated workbooks live in a temp dir keyed by a random id, so a download
# link can't be guessed and nothing persists beyond the process.
_EXPORT_DIR = Path(tempfile.gettempdir()) / "converge_flights_exports"
_EXPORT_DIR.mkdir(parents=True, exist_ok=True)


def _split(raw: str) -> list[str]:
    """Split a comma/space separated field into upper-cased codes."""
    return [
        part.strip().upper() for part in raw.replace(",", " ").split() if part.strip()
    ]


def build_config(
    *,
    travelers_raw: str,
    destination: str,
    windows_raw: str,
    provider: str,
    max_stops: int,
    max_duration_hours: float,
    currency: str,
    cabin: str,
) -> Config:
    """Turn the submitted form fields into a validated :class:`Config`.

    ``travelers_raw`` is one traveler per line as ``Name: JFK, EWR``.
    ``windows_raw`` is one window per line as ``2026-08-13 to 2026-08-16``
    (any separator containing "to", a comma, or whitespace works).
    """
    travelers: list[dict[str, Any]] = []
    for line in travelers_raw.splitlines():
        line = line.strip()
        if not line:
            continue
        name, _, airports = line.partition(":")
        codes = _split(airports)
        if not name.strip() or not codes:
            raise ValueError(f"Traveler line {line!r} must look like 'Alex: JFK, EWR'.")
        travelers.append({"name": name.strip(), "origin_airports": codes})
    if not travelers:
        raise ValueError("Add at least one traveler, e.g. 'Alex: JFK, EWR'.")

    windows: list[dict[str, str]] = []
    for line in windows_raw.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = [p for p in line.replace(" to ", " ").replace(",", " ").split() if p]
        if len(parts) != 2:
            raise ValueError(
                f"Date line {line!r} must look like '2026-08-13 to 2026-08-16'."
            )
        try:
            depart = date.fromisoformat(parts[0])
            return_ = date.fromisoformat(parts[1])
        except ValueError as exc:
            raise ValueError(f"Dates in {line!r} must be YYYY-MM-DD.") from exc
        if return_ <= depart:
            raise ValueError(f"Return must be after departure in {line!r}.")
        windows.append({"depart": parts[0], "return": parts[1]})
    if not windows:
        raise ValueError("Add at least one date window.")

    return Config.model_validate(
        {
            "travelers": travelers,
            "destination": destination.strip().upper(),
            "date_windows": {"explicit": windows},
            "constraints": {
                "max_stops": max_stops,
                "max_duration_hours": max_duration_hours,
                "currency": currency.strip().upper(),
                "cabin": cabin,
            },
            "provider": provider,
        }
    )


def run_search(config: Config) -> ComparisonReport:
    """Execute the search for ``config`` using the configured provider(s)."""
    providers = build_providers(config, cache=QueryCache(config.cache))
    windows = build_windows(config.date_windows)
    return compare(providers, windows, config)


def _past_dates(config: Config) -> list[str]:
    """Windows whose departure is already in the past (providers return none)."""
    today = date.today()
    return [
        w.depart.isoformat()
        for w in build_windows(config.date_windows)
        if w.depart < today
    ]


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> Any:
    """Render the search form with sensible defaults (dates always future)."""
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"defaults": form_defaults(), "error": None},
    )


@app.get("/healthz")
def healthz() -> dict[str, str]:
    """Liveness probe for hosting platforms."""
    return {"status": "ok"}


@app.post("/search", response_class=HTMLResponse)
def search(  # noqa: PLR0913 - a form post genuinely has this many fields
    request: Request,
    travelers: str = Form(...),
    destination: str = Form(...),
    windows: str = Form(...),
    provider: str = Form("serpapi"),
    max_stops: int = Form(1),
    max_duration_hours: float = Form(10.0),
    currency: str = Form("USD"),
    cabin: str = Form("economy"),
) -> Any:
    """Validate the form, run the search, and render the ranked comparison."""
    submitted = {
        "travelers": travelers,
        "destination": destination,
        "windows": windows,
        "provider": provider,
        "max_stops": max_stops,
        "max_duration_hours": max_duration_hours,
        "currency": currency,
        "cabin": cabin,
    }

    def fail(message: str) -> Any:
        return templates.TemplateResponse(
            request=request,
            name="index.html",
            context={"defaults": submitted, "error": message},
            status_code=400,
        )

    try:
        config = build_config(
            travelers_raw=travelers,
            destination=destination,
            windows_raw=windows,
            provider=provider,
            max_stops=max_stops,
            max_duration_hours=max_duration_hours,
            currency=currency,
            cabin=cabin,
        )
    except ValueError as exc:
        return fail(str(exc))

    try:
        report = run_search(config)
    except MissingCredentialsError as exc:
        return fail(f"Missing credentials: {exc}")
    except ProviderError as exc:
        return fail(f"Provider error: {exc}")
    except httpx.TransportError as exc:
        return fail(
            f"Network error reaching the provider ({type(exc).__name__}: {exc}). "
            "Check the server's connection and any egress policy."
        )

    traveler_names = [t.name for t in config.travelers]
    token = uuid.uuid4().hex
    export_report(report, traveler_names, _EXPORT_DIR / f"{token}.xlsx")

    return templates.TemplateResponse(
        request=request,
        name="results.html",
        context={
            "report": report,
            "ranked": report.ranked(),
            "traveler_names": traveler_names,
            "destination": config.destination,
            "providers": ", ".join(config.selected_providers),
            "download_token": token,
            "baseline": report.most_expensive_total(),
            "past_dates": _past_dates(config),
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        },
    )


@app.get("/download/{token}")
def download(token: str) -> FileResponse:
    """Serve a previously generated workbook by its opaque token."""
    # Reject anything that isn't a plain hex token so the path can't escape.
    if not token.isalnum():
        raise _not_found()
    path = _EXPORT_DIR / f"{token}.xlsx"
    if not path.is_file():
        raise _not_found()
    return FileResponse(
        path,
        media_type=("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        filename="converge-flights.xlsx",
    )


def _not_found() -> Exception:
    from fastapi import HTTPException

    return HTTPException(status_code=404, detail="Export not found or expired.")


def default_windows(count: int = 2) -> str:
    """Suggest the next few Thursday→Sunday windows, always in the future."""
    today = date.today()
    first = today + timedelta(days=((3 - today.weekday()) % 7) or 7)
    return "\n".join(
        f"{(d := first + timedelta(weeks=i)).isoformat()} to "
        f"{(d + timedelta(days=3)).isoformat()}"
        for i in range(count)
    )


def form_defaults() -> dict[str, Any]:
    """Fresh form defaults; computed per request so dates never go stale."""
    return {
        "travelers": "Alex: LGA, JFK, EWR\nSam: DTW",
        "destination": "DEN",
        "windows": default_windows(),
        "provider": "serpapi",
        "max_stops": 1,
        "max_duration_hours": 10.0,
        "currency": "USD",
        "cabin": "economy",
    }


__all__ = ["app", "build_config", "default_windows", "form_defaults", "run_search"]
