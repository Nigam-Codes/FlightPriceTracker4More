# converge-flights

Find and compare group flights when everyone departs from a **different** city.

A couple (or a larger group) lives apart and wants to vacation together. Each
person has one or more home airports. `converge-flights` searches round-trip
fares for every person to a shared destination across a range of candidate
date windows, applies shared constraints, and produces a comparison showing
**total group cost per date option** plus per-person breakdowns — so the group
can pick the cheapest weekend to travel.

- **Two providers, one model:** Amadeus (Self-Service) and Kiwi.com (Tequila).
  Run either, or run **both** and keep the cheapest qualifying offer per
  traveler across providers.
- **Provider-agnostic filtering:** every raw response is normalized into a
  shared `Offer`; constraints (stops, duration, departure/arrival times) are
  enforced on that normalized layer, so filtering is identical regardless of
  source.
- **Outputs:** a ranked terminal table (`rich`), an Excel workbook with charts
  (`openpyxl`), and an optional raw-JSON dump per provider.

---

## Install

Requires **Python 3.11+**.

```bash
git clone https://github.com/Nigam-Codes/FlightPriceTracker4More.git
cd FlightPriceTracker4More
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"      # drop [dev] if you don't need tests/linters
```

## Get free API keys (no credit card for either)

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

### Provide the credentials

Copy `.env.example` to `.env` and fill in the values (the file is git-ignored):

```bash
cp .env.example .env
$EDITOR .env
```

```dotenv
AMADEUS_CLIENT_ID=xxxxxxxxxxxxxxxx
AMADEUS_CLIENT_SECRET=xxxxxxxxxxxxxxxx
KIWI_API_KEY=xxxxxxxxxxxxxxxx
# Optional: live Amadeus fares
# AMADEUS_BASE_URL=https://api.amadeus.com
```

If the selected provider's key is missing, the tool fails with a clear
message telling you exactly which variable to set.

## Configure

Copy the example and edit it:

```bash
cp config.example.yaml config.yaml
```

The bundled example encodes the reference scenario: a couple — NYC
(**LGA/JFK/EWR**) and Detroit (**DTW**) — flying to Denver (**DEN**), every
Thursday-afternoon-to-Sunday-evening weekend from September to December, at
most one stop, at most ten hours per direction, running **both** providers.

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

provider: both                          # amadeus | kiwi | both
```

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
└── providers/
    ├── base.py          # FlightProvider protocol + HTTP backoff helper
    ├── amadeus.py       # Amadeus Self-Service provider
    └── kiwi.py          # Kiwi.com (Tequila) provider
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
