"""CLI error handling: failures must report cleanly, not dump tracebacks."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from converge_flights import cli

runner = CliRunner()

CONFIG = """
travelers:
  - name: Alex
    origin_airports: [JFK]
destination: DEN
date_windows:
  explicit:
    - depart: 2026-08-13
      return: 2026-08-16
constraints:
  currency: USD
provider: serpapi
cache:
  enabled: false
"""


def _write_config(tmp_path: Path) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(CONFIG, encoding="utf-8")
    return path


def test_network_error_reports_cleanly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A transport failure surfaces a readable message, not a traceback."""
    monkeypatch.setenv("SERPAPI_API_KEY", "fake-key")

    def boom(*args: object, **kwargs: object) -> httpx.Response:
        raise httpx.ProxyError("403 Forbidden")

    # Fail fast: no real sleeping between retries.
    monkeypatch.setattr(cli, "compare", lambda *a, **k: boom())

    result = runner.invoke(
        cli.app,
        [
            "search",
            "--config",
            str(_write_config(tmp_path)),
            "--out",
            str(tmp_path / "out.xlsx"),
        ],
    )
    assert result.exit_code == 5
    assert "Network error reaching the provider" in result.output
    assert "ProxyError" in result.output


def test_missing_credentials_reports_cleanly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No API key -> exit 3 with an actionable message."""
    monkeypatch.delenv("SERPAPI_API_KEY", raising=False)
    result = runner.invoke(
        cli.app,
        [
            "search",
            "--config",
            str(_write_config(tmp_path)),
            "--out",
            str(tmp_path / "out.xlsx"),
        ],
    )
    assert result.exit_code == 3
    assert "Missing credentials" in result.output
    assert "SERPAPI_API_KEY" in result.output
