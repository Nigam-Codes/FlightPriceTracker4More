# converge-flights

Find and compare group flights when everyone departs from a **different** city.

A couple (or a larger group) lives apart and wants to vacation together. Each
person has one or more home airports. `converge-flights` searches round-trip
fares for every person to a shared destination across a range of candidate
date windows, applies shared constraints, and produces a comparison showing
**total group cost per date option** plus per-person breakdowns — so the group
can pick the cheapest weekend to travel.

- **Four providers, one model:** SerpApi (Google Flights), Duffel, and the
  legacy Amadeus/Kiwi adapters. Run any one, or a list of several, and keep the
  cheapest qualifying offer per traveler across all of them.
- **Provider-agnostic filtering:** every raw response is normalized into a
  shared `Offer`; constraints (stops, duration, departure/arrival times) are
  enforced on that normalized layer, so filtering is identical regardless of
  source.
- **Outputs:** a ranked terminal table (`rich`), an Excel workbook with charts
  (`openpyxl`), and an optional raw-JSON dump per provider.
- **CLI or web app:** run it from the terminal, or serve the same engine as a
  small FastAPI web app you can host (see [Web app](#web-app-hosted)).

---

## The flights-API landscape (why these providers)

Real fare data comes from a few kinds of source, and this tool plugs any of them
in behind one interface:

- **Metasearch as an API** — **SerpApi's Google Flights engine** returns real,
  current Google Flights fares as licensed structured JSON (no scraping). This
  is the recommended default.
- **Direct airline / NDC** — modern APIs such as **Duffel** blend airline + NDC
  content over clean JSON REST with an instant self-serve token.
- **GDS** (Amadeus, Sabre, Travelport) and **aggregators** (Kiwi/Tequila) — the
  original adapters. **As of 2026 these are largely closed**: the Amadeus
  Self-Service sandbox shut down (2026-07-17) and Kiwi/Tequila is now
  partner-invite only. The adapters remain for anyone who still has access, but
  are no longer the default path.

> **Scraping sites like Expedia or Google Flights directly is intentionally not
> supported** — it violates their terms of service, is defeated by anti-bot
> defenses, and is unreliable. SerpApi is the licensed, compliant way to get the
> same web-sourced fares.

`converge-flights` ships adapters for all four; each normalizes into the same
`Offer`, so adding another source is just another adapter. **Booking flows**
(pricing confirmation, PNR creation, ticketing, post-booking changes) are out of
scope: this tool is search-and-compare only.

---

## Install

Requires **Python 3.11+**.

```bash
git clone https://github.com/Nigam-Codes/FlightPriceTracker4More.git
cd FlightPriceTracker4More
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"      # drop [dev] if you don't need tests/linters
```

## Get a free API key (no credit card)

### SerpApi — Google Flights (recommended)

1. Sign up at <https://serpapi.com/users/sign_up> (free plan, ~100 searches/month,
   no card).
2. Verify your email (phone verification may be required to activate searches).
3. Copy your key from <https://serpapi.com/manage-api-key> into `SERPAPI_API_KEY`.

Each round-trip weekend costs ~2 SerpApi calls (outbound + return leg), and
results are cached on disk so re-runs don't burn quota.

### Duffel

1. Create a free account at <https://app.duffel.com> (no card).
2. Copy an **access token** into `DUFFEL_API_TOKEN` (test `duffel_test_…` =
   sandbox; live `duffel_live_…` = real fares).

### Legacy: Amadeus / Kiwi (largely closed in 2026)

> The Amadeus Self-Service sandbox shut down (2026-07-17) and Kiwi/Tequila is
> partner-invite only. These sections apply only if you already have access.

### Amadeus (Self-Service)

1. Register at <https://developers.amadeus.com> and confirm your email.
2. Open **My Self-Service Workspace → Create New App**.
3. Copy the app's **API Key** and **API Secret** — these are your
   `AMADEUS_CLIENT_ID` and `AMADEUS_CLIENT_SECRET`.
4. New apps start against the **test** environment, which is instant and free.

> ⚠️ **Test vs. production fares.** The Amadeus Self-Service **test**
> environment returns *cached / limited* fares — great for development, but not
> live prices. To get live fares, move your app to production in the Amadeus
> portal, use the production key, and set
> `AMADEUS_BASE_URL=https://api.amadeus.com`.

### Kiwi.com (Tequila)

1. Sign in at <https://tequila.kiwi.com/portal/login/apikeys>.
2. Create a **Search API** key — issued instantly, no card required.
3. Copy it into `KIWI_API_KEY`.

You only need keys for the provider(s) you actually run.

### Provide the credentials

Copy `.env.example` to `.env` and fill in the values (the file is git-ignored):

```bash
cp .env.example .env
$EDITOR .env
```

```dotenv
SERPAPI_API_KEY=xxxxxxxxxxxxxxxx
DUFFEL_API_TOKEN=duffel_test_xxxxxxxxxxxxxxxx
# Legacy (only if you still have access):
AMADEUS_CLIENT_ID=xxxxxxxxxxxxxxxx
AMADEUS_CLIENT_SECRET=xxxxxxxxxxxxxxxx
KIWI_API_KEY=xxxxxxxxxxxxxxxx
# Optional: live Amadeus fares
# AMADEUS_BASE_URL=https://api.amadeus.com
```

If the selected provider's key is missing, the tool fails with a clear
message telling you exactly which variable to set.

## Web app (hosted)

Prefer a browser to a terminal? The same engine ships as a small FastAPI app:
a form describes the group and the trip, the **server** runs the search, and you
get the ranked comparison plus the `.xlsx` download.

```bash
pip install -e ".[web]"
uvicorn converge_flights.web:app --reload     # http://127.0.0.1:8000
```

**API keys stay on the server.** The page posts a plain HTML form; keys are read
from the server's environment and never reach the browser (never put a SerpApi
key in client-side JavaScript).

### Deploy it

The repo includes ready-made config — set `SERPAPI_API_KEY` in the host's
environment (never commit it):

| Host | How |
| --- | --- |
| **Render** | `render.yaml` is a blueprint: New → Blueprint → point at this repo. It prompts for `SERPAPI_API_KEY`. |
| **Docker** (Fly.io, Cloud Run, anywhere) | `docker build -t converge-flights .` then `docker run -p 8000:8000 -e SERPAPI_API_KEY=... converge-flights` |
| **Heroku-style** | `Procfile` is included. |

Routes: `/` (form), `/search` (results), `/download/{token}` (workbook),
`/healthz` (liveness probe), `/api/docs` (OpenAPI).

## Configure

Copy the example and edit it:

```bash
cp config.example.yaml config.yaml
```

The bundled example encodes the reference scenario: a couple — NYC
(**LGA/JFK/EWR**) and Detroit (**DTW**) — flying to Denver (**DEN**), every
Thursday-afternoon-to-Sunday-evening weekend from September to December, at
most one stop, at most ten hours per direction, running **all three** providers.

```yaml
travelers:
  - name: Alex
    origin_airports: [LGA, JFK, EWR]   # cheapest qualifying one wins
  - name: Sam
    origin_airports: [DTW]

destination: DEN

date_windows:
  recurring:                            # every Thursday -> the next Sunday
    depart_weekday: thursday
    return_weekday: sunday
    start: 2025-09-11
    end: 2025-12-31

constraints:
  max_stops: 1
  max_duration_hours: 10                # per direction
  depart_time_window: { start: "12:00", end: "18:00" }   # Thursday afternoon
  return_arrive_by: "21:00"             # Sunday evening
  cabin: economy
  currency: USD

# Single value (serpapi | duffel | amadeus | kiwi | both), or a list to run
# several and keep the cheapest qualifying offer per traveler across all of them:
provider: [serpapi, duffel]
```

`provider: both` remains a backward-compatible alias for `[amadeus, kiwi]`.

Prefer explicit dates? Swap the `recurring` block for an `explicit` list
(set exactly one of the two):

```yaml
date_windows:
  explicit:
    - depart: 2025-09-11
      return: 2025-09-14
    - depart: 2025-09-18
      return: 2025-09-21
```

## Run

```bash
converge-flights search --config config.yaml --out results.xlsx
```

Options:

| Flag | Meaning |
| --- | --- |
| `--config, -c` | Path to your `config.yaml` (required). |
| `--out, -o` | Output `.xlsx` path (default `results.xlsx`). |
| `--dump DIR` | Also write raw provider JSON to `DIR/raw_<provider>.json`. |
| `--no-cache` | Bypass the on-disk cache for this run. |

### Sample output

```
                 Group flight comparison (USD) — cheapest first
┏━━━┳━━━━━━━━━━━━┳━━━━━━━━━━━━┳━━━━━━━━━━━━━┳━━━━━━━━━━━━━┳━━━━━━━━━━━━┳━━━━━━━┓
┃ # ┃ Depart     ┃ Return     ┃ Group total ┃        Alex ┃        Sam ┃ Notes ┃
┡━━━╇━━━━━━━━━━━━╇━━━━━━━━━━━━╇━━━━━━━━━━━━━╇━━━━━━━━━━━━━╇━━━━━━━━━━━━╇━━━━━━━┩
│ 1 │ 2025-09-11 │ 2025-09-14 │      480.00 │ 280 kiwi/JFK│ 200 ama/DTW│       │
│ 2 │ 2025-09-18 │ 2025-09-21 │      610.00 │ 350 ama/EWR │ 260 kiwi/DT│       │
└───┴────────────┴────────────┴─────────────┴─────────────┴────────────┴───────┘
```

A traveler with **zero** qualifying options in a window is flagged in the
`Notes` column, and that window is pushed to the bottom of the ranking.

### The Excel workbook

`results.xlsx` contains three tabs:

- **Fares** — one row per traveler × window best offer (provider, winning
  origin, price, stops, per-leg duration, carrier).
- **Comparison** — windows ranked by group total, cheapest origin/provider per
  traveler, and savings versus the most expensive window.
- **Dashboard** — a bar chart of group cost by window and a line chart of each
  traveler's cost trend across windows.

## Caching & rate limits

Re-runs are served from an on-disk cache (JSON keyed by a hash of the query),
so repeated searches don't burn free-tier quota. Configure it under `cache:`
in `config.yaml`, or bypass it with `--no-cache`.

Provider rate limits are documented in the source:

- **Amadeus** (`converge_flights/providers/amadeus.py`): ~10 req/s plus a
  monthly free quota; OAuth2 tokens are cached and refreshed automatically, and
  every request retries on 429/5xx with exponential backoff (honoring
  `Retry-After`).
- **Kiwi/Tequila** (`converge_flights/providers/kiwi.py`): a per-key throughput
  limit plus a monthly free quota; same backoff/retry behavior.

## Project layout

```
converge_flights/
├── models.py            # normalized Offer / Leg / comparison models
├── config.py            # pydantic config loaded from config.yaml
├── dates.py             # recurring-rule -> concrete date windows
├── filters.py           # constraint enforcement on the Offer layer
├── compare.py           # search orchestration + group comparison
├── export.py            # .xlsx export with charts (openpyxl)
├── cache.py             # on-disk query cache
├── cli.py               # typer CLI (converge-flights search ...)
├── web.py               # FastAPI web app (form -> results -> xlsx)
├── templates/           # Jinja templates for the web app
└── providers/
    ├── base.py          # FlightProvider protocol + HTTP backoff helper
    ├── amadeus.py       # Amadeus Self-Service provider (legacy)
    ├── kiwi.py          # Kiwi.com (Tequila) provider (legacy)
    ├── duffel.py        # Duffel (airline + NDC) provider
    └── serpapi.py       # SerpApi Google Flights provider (recommended)
tests/                   # fully offline, recorded fixtures for both providers
config.example.yaml
```

## Development

```bash
ruff check converge_flights tests      # lint
ruff format converge_flights tests     # format
mypy converge_flights                  # strict type checking
pytest                                 # offline tests (recorded fixtures)
```

Tests never make live API calls: provider fetch paths are exercised with
`httpx.MockTransport` against recorded Amadeus and Kiwi responses, and a
normalization test proves both providers map to **identical** `Offer` fields.
CI (`.github/workflows/ci.yml`) runs ruff, mypy, and pytest on every push
across Python 3.11 and 3.12.

## License

[MIT](LICENSE).
