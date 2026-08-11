from __future__ import annotations

from collections.abc import Iterable

import exchange_calendars as xcals
import pandas as pd


def completed_month_ends(
    index: pd.Index,
    allowed_months: Iterable[int] | None = None,
) -> pd.DatetimeIndex:
    """Return observed dates that are actual completed XNYS month-end sessions."""
    dates = pd.DatetimeIndex(index)
    if dates.empty:
        return pd.DatetimeIndex([])
    if dates.tz is not None:
        dates = dates.tz_convert(None)
    dates = pd.DatetimeIndex(sorted(set(dates.normalize())))

    calendar = xcals.get_calendar("XNYS")
    calendar_end = dates[-1].to_period("M").end_time.normalize()
    sessions = calendar.sessions_in_range(dates[0], calendar_end)
    if sessions.tz is not None:
        sessions = sessions.tz_convert(None)
    sessions = sessions.normalize()

    observed = pd.Series(dates, index=dates).groupby(dates.to_period("M")).last()
    scheduled = pd.Series(sessions, index=sessions).groupby(sessions.to_period("M")).last()
    allowed = set(allowed_months) if allowed_months is not None else None
    complete = [
        observed_date
        for period, observed_date in observed.items()
        if period in scheduled
        and observed_date == scheduled.loc[period]
        and (allowed is None or period.month in allowed)
    ]
    return pd.DatetimeIndex(complete)
