"""School context: what period is it at this location right now?

Context comes from the school timetable (class / break / lunch / after
school), location-specific overrides (PE periods for changing rooms, trips
for buses) and cleaning schedules.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from zoneinfo import ZoneInfo

DEFAULT_TIMETABLE = [
    {"start": "06:30", "end": "07:30", "type": "before_school", "label": "Arrival"},
    {"start": "07:30", "end": "09:30", "type": "class", "label": "Periods 1-2"},
    {"start": "09:30", "end": "09:50", "type": "break", "label": "Morning break"},
    {"start": "09:50", "end": "11:50", "type": "class", "label": "Periods 3-4"},
    {"start": "11:50", "end": "12:30", "type": "lunch", "label": "Lunch"},
    {"start": "12:30", "end": "14:30", "type": "class", "label": "Periods 5-6"},
    {"start": "14:30", "end": "17:00", "type": "after_school", "label": "After school"},
]
SCHOOL_DAYS = [0, 1, 2, 3, 4]  # Mon-Fri (UAE)

PERIOD_LABELS = {
    "class": "Class time", "break": "Break time", "lunch": "Lunch time",
    "after_school": "After school", "before_school": "Before school", "off_hours": "Outside school hours",
    "pe_period": "PE period", "trip_home": "Trip home", "trip_to_school": "Trip to school",
    "weekend": "Weekend",
}


@dataclass
class Context:
    period: str
    label: str
    cleaning: bool = False
    local_time: str = ""

    def describe(self) -> str:
        s = PERIOD_LABELS.get(self.period, self.period)
        return s + (" · cleaning scheduled" if self.cleaning else "")


def _t(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


def _in(slot: dict, t: time, weekday: int) -> bool:
    days = slot.get("days")
    if days is not None and weekday not in days:
        return False
    return _t(slot["start"]) <= t < _t(slot["end"])


def resolve_context(ts: datetime, school: dict, location: dict) -> Context:
    tz = ZoneInfo(school.get("timezone", "Asia/Dubai"))
    local = ts.astimezone(tz)
    t, wd = local.time(), local.weekday()
    settings = school.get("settings", {})
    cleaning = any(_in(s, t, wd) for s in location.get("cleaning", []))
    hhmm = local.strftime("%a %H:%M")

    for slot in location.get("schedule", []):  # location overrides win (PE, bus trips)
        if _in(slot, t, wd):
            return Context(slot["type"], slot.get("label", PERIOD_LABELS.get(slot["type"], "")), cleaning, hhmm)
    if wd not in settings.get("school_days", SCHOOL_DAYS):
        return Context("weekend", "Weekend", cleaning, hhmm)
    for slot in settings.get("timetable", DEFAULT_TIMETABLE):
        if _in(slot, t, wd):
            return Context(slot["type"], slot.get("label", ""), cleaning, hhmm)
    return Context("off_hours", "Outside school hours", cleaning, hhmm)
