"""Report filters: a date range (presets or custom) and an optional plan."""

import datetime
from dataclasses import dataclass
from urllib.parse import urlencode

from apps.catalog.models import Plan

PRESETS = [
    ("month", "This month"),
    ("30d", "Last 30 days"),
    ("quarter", "This quarter"),
    ("year", "This year"),
    ("all", "All time"),
    ("custom", "Custom"),
]


@dataclass(frozen=True)
class Filters:
    preset: str
    start: object  # date or None (no lower bound)
    end: object  # date or None
    plan_id: object  # int or None
    error: str = ""

    @property
    def label(self) -> str:
        if self.start is None and self.end is None:
            return "All time"
        return f"{self.start:%d %b %Y} to {self.end:%d %b %Y}"

    @property
    def querystring(self) -> str:
        params = {"preset": self.preset}
        if self.preset == "custom":
            params["from"] = self.start.isoformat() if self.start else ""
            params["to"] = self.end.isoformat() if self.end else ""
        if self.plan_id:
            params["plan"] = self.plan_id
        return urlencode(params)


def _range(preset, today):
    if preset == "30d":
        return today - datetime.timedelta(days=29), today
    if preset == "quarter":
        first_month = 3 * ((today.month - 1) // 3) + 1
        return today.replace(month=first_month, day=1), today
    if preset == "year":
        return today.replace(month=1, day=1), today
    if preset == "all":
        return None, None
    return today.replace(day=1), today  # "month" and the fallback


def parse(params, today) -> Filters:
    preset = params.get("preset") or "month"
    if preset not in dict(PRESETS):
        preset = "month"

    plan_id = None
    raw_plan = params.get("plan")
    if raw_plan and str(raw_plan).isdigit() and Plan.objects.filter(pk=int(raw_plan)).exists():
        plan_id = int(raw_plan)

    error = ""
    if preset == "custom":
        try:
            start = datetime.date.fromisoformat(params.get("from", ""))
            end = datetime.date.fromisoformat(params.get("to", ""))
            if start > end:
                raise ValueError
            return Filters("custom", start, end, plan_id)
        except ValueError:
            error = "Enter a valid date range, with the start date not after the end date."
            preset = "month"
    start, end = _range(preset, today)
    return Filters(preset, start, end, plan_id, error)
