# Container image for the converge-flights web app.
#
# Build:  docker build -t converge-flights .
# Run:    docker run -p 8000:8000 -e SERPAPI_API_KEY=... converge-flights
#
# API keys are supplied at runtime via the environment and are never baked in.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8000

WORKDIR /app

# Install dependencies first so layer caching survives source edits.
COPY pyproject.toml README.md ./
COPY converge_flights ./converge_flights
RUN pip install --no-cache-dir ".[web]"

# Cache directory for provider responses (keeps free-tier quota intact).
ENV CONVERGE_FLIGHTS_CACHE=/app/.cache
RUN mkdir -p /app/.cache

EXPOSE 8000
# Hosting platforms inject $PORT; default to 8000 locally.
CMD ["sh", "-c", "uvicorn converge_flights.web:app --host 0.0.0.0 --port ${PORT:-8000}"]
