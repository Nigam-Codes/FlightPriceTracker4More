"""Expand configured date windows into concrete depart/return pairs."""

from __future__ import annotations

from datetime import date, timedelta

from converge_flights.config import DateWindowsConfig, RecurringRule
from converge_flights.models import DateWindow


def _next_weekday_on_or_after(start: date, weekday: int) -> date:
    """Return the first date >= ``start`` falling on ``weekday`` (Mon=0)."""
    delta = (weekday - start.weekday()) % 7
    return start + timedelta(days=delta)


def expand_recurring(rule: RecurringRule) -> list[DateWindow]:
    """Generate every depart/return window implied by a weekly rule.

    Walks week by week from ``rule.start`` to ``rule.end``. For each depart
    weekday in range, the return is the next occurrence of the return weekday
    on or after the departure (so Thursday → Sunday spans the same weekend).
    Windows whose return falls past ``rule.end`` are dropped.
    """
    windows: list[DateWindow] = []
    depart = _next_weekday_on_or_after(rule.start, rule.depart_dow)
    while depart <= rule.end:
        ret = _next_weekday_on_or_after(depart, rule.return_dow)
        # If depart and return share a weekday, treat return as a full week later.
        if ret <= depart:
            ret = ret + timedelta(days=7)
        if ret <= rule.end:
            windows.append(DateWindow(depart=depart, return_=ret))
        depart = depart + timedelta(days=7)
    return windows


def build_windows(cfg: DateWindowsConfig) -> list[DateWindow]:
    """Resolve a :class:`DateWindowsConfig` into concrete windows."""
    if cfg.recurring is not None:
        return expand_recurring(cfg.recurring)
    assert cfg.explicit is not None  # guaranteed by config validation
    return [DateWindow(depart=w.depart, return_=w.return_) for w in cfg.explicit]


__all__ = ["build_windows", "expand_recurring"]
