"""Billing-period arithmetic. Pure functions.

Monthly and yearly periods stay anchored to the subscription's start day, so a subscription
that starts on 31 Jan is billed 31 Jan, 28 Feb, 31 Mar (not 28 Feb, 28 Mar, 28 Apr).
"""

import calendar
import datetime


def add_months(d: datetime.date, months: int, anchor_day: int | None = None) -> datetime.date:
    year, month0 = divmod(d.year * 12 + (d.month - 1) + months, 12)
    month = month0 + 1
    day = min(anchor_day or d.day, calendar.monthrange(year, month)[1])
    return datetime.date(year, month, day)


def next_period_start(
    start: datetime.date, billing_period: str, anchor: datetime.date
) -> datetime.date:
    """First day of the period that follows the one starting on `start`."""
    if billing_period == "daily":
        return start + datetime.timedelta(days=1)
    if billing_period == "weekly":
        return start + datetime.timedelta(days=7)
    if billing_period == "monthly":
        return add_months(start, 1, anchor.day)
    if billing_period == "yearly":
        return add_months(start, 12, anchor.day)
    raise ValueError(f"Unknown billing period: {billing_period}")


def period_end(start: datetime.date, billing_period: str, anchor: datetime.date) -> datetime.date:
    """Last day of the period that starts on `start`."""
    return next_period_start(start, billing_period, anchor) - datetime.timedelta(days=1)
