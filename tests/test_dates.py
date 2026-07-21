"""Date-window expansion from recurring rules and explicit lists."""

from __future__ import annotations

from datetime import date

from converge_flights.config import DateWindowsConfig, ExplicitWindow, RecurringRule
from converge_flights.dates import build_windows, expand_recurring


def test_recurring_thursday_to_sunday() -> None:
    rule = RecurringRule(
        depart_weekday="thursday",
        return_weekday="sunday",
        start=date(2025, 9, 11),  # a Thursday
        end=date(2025, 12, 31),
    )
    windows = expand_recurring(rule)
    assert windows[0].depart == date(2025, 9, 11)
    assert windows[0].return_ == date(2025, 9, 14)
    # Every window departs Thursday (weekday 3) and returns Sunday (weekday 6).
    for w in windows:
        assert w.depart.weekday() == 3
        assert w.return_.weekday() == 6
        assert w.return_ > w.depart
        assert (w.return_ - w.depart).days == 3


def test_recurring_respects_end_boundary() -> None:
    rule = RecurringRule(
        depart_weekday="thursday",
        return_weekday="sunday",
        start=date(2025, 9, 11),
        end=date(2025, 9, 20),  # only the 9/11-9/14 weekend fits fully
    )
    windows = expand_recurring(rule)
    assert len(windows) == 1
    assert windows[0].return_ == date(2025, 9, 14)


def test_build_windows_explicit() -> None:
    cfg = DateWindowsConfig(
        explicit=[
            ExplicitWindow(depart=date(2025, 9, 11), **{"return": date(2025, 9, 14)}),
        ]
    )
    windows = build_windows(cfg)
    assert len(windows) == 1
    assert windows[0].depart == date(2025, 9, 11)
    assert windows[0].return_ == date(2025, 9, 14)


def test_start_not_on_depart_weekday_advances() -> None:
    # Start Monday 2025-09-08; first Thursday is 2025-09-11.
    rule = RecurringRule(
        depart_weekday="thursday",
        return_weekday="sunday",
        start=date(2025, 9, 8),
        end=date(2025, 9, 30),
    )
    windows = expand_recurring(rule)
    assert windows[0].depart == date(2025, 9, 11)
