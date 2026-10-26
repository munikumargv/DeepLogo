"""Synthetic sensor traces and the reference scenarios from the proposal."""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

from .edge.sensor import Reading

BASE = dict(pm1=4.0, pm2_5=6.0, pm10=9.0, voc_index=100.0, nox_index=1.0, rh=45.0)

# amplitude above baseline per puff, and decay time constant (s)
PROFILES = {
    "vape":   dict(pm1=95, pm2_5=100, pm10=105, voc_index=45, nox_index=1, rh=2.5, tau=18, puffs=3),
    "smoke":  dict(pm1=55, pm2_5=85, pm10=120, voc_index=70, nox_index=30, rh=0.1, tau=55, puffs=3),
    "air_freshener": dict(pm1=8, pm2_5=11, pm10=13, voc_index=170, nox_index=0, rh=0.3, tau=25, puffs=1),
    "clean": dict(pm1=0, pm2_5=0, pm10=0, voc_index=0, nox_index=0, rh=0, tau=1, puffs=0),
}


def sensor_trace(profile: str, t0: float, seed: int = 7, pre_s: int = 60, event_s: int = 150) -> list[Reading]:
    """1 Hz readings: quiet baseline, a few puffs ~15 s apart with exponential decay, then quiet."""
    rnd = random.Random(seed)
    p = PROFILES[profile]
    puffs = [pre_s + 15 * i for i in range(p["puffs"])]
    out = []
    for s in range(pre_s + event_s):
        vals = {}
        for k, base in BASE.items():
            amp = sum(p[k] * math.exp(-(s - t) / p["tau"]) for t in puffs if s >= t)
            noise = rnd.gauss(0, 0.6 if k.startswith("pm") else 0.4)
            vals[k] = max(0.0, base + amp + noise)
        out.append(Reading(ts=t0 + s, **vals))
    return out


@dataclass
class Scenario:
    id: str
    title: str
    location_id: str
    weekday: int               # 0 = Monday
    local_time: str            # HH:MM, school timezone
    utterances: list[str] = field(default_factory=list)
    sensor: str | None = None
    cues: list[tuple[str, int]] = field(default_factory=list)  # (cue, minutes relative to alert)
    priors: list[tuple[int, str]] = field(default_factory=list)  # (days before, HH:MM) earlier same detections
    expected_score: int = 0
    expected_level: str = ""
    expected_action: str = ""


SCENARIOS = [
    Scenario("vape_washroom", "Vaping in washroom", "wr-b1", 1, "10:20", sensor="vape",
             cues=[("group_entry", -12), ("long_dwell", -1)],
             expected_score=88, expected_level="high", expected_action="Alert supervisor"),
    Scenario("drug_deal", "Drug deal behind building", "out-back", 1, "15:10",
             utterances=["you got the weed", "how much for it"],
             cues=[("small_group", -3), ("hand_exchange", 0)],
             expected_score=90, expected_level="critical", expected_action="Alert security"),
    Scenario("smoke_stairwell", "Smoking in stairwell", "st-east", 1, "09:35",
             utterances=["hide it quick"], sensor="smoke", cues=[("gathering", -4)],
             expected_score=85, expected_level="high", expected_action="Alert supervisor"),
    Scenario("vape_bus", "Vaping on school bus", "bus-12", 1, "15:00", sensor="vape",
             cues=[("back_seat", -2)],
             expected_score=80, expected_level="high", expected_action="Alert bus attendant"),
    Scenario("vape_changing_room", "Vaping in changing room", "cr-boys", 1, "10:05",
             utterances=["guys the teacher is coming"], sensor="vape", cues=[("fast_exit", 1)],
             expected_score=84, expected_level="high", expected_action="Alert PE teacher"),
    Scenario("canteen_talk", "Drug talk in canteen", "canteen", 1, "12:05",
             utterances=["did you see that movie about cocaine"], cues=[("normal_activity", 0)],
             expected_score=35, expected_level="low", expected_action="Log only, no alert"),
    Scenario("repeated_washroom", "Repeated alerts in same washroom", "wr-a2", 1, "11:00", sensor="vape",
             priors=[(5, "11:05"), (4, "10:55"), (1, "11:10")],
             expected_score=92, expected_level="critical", expected_action="Report to school management"),
    Scenario("air_freshener", "Air freshener spray", "wr-c0", 1, "10:25", sensor="air_freshener",
             cues=[("cleaner_entry", -1)],
             expected_score=15, expected_level="low", expected_action="No alert"),
]

DEMO_LOCATIONS = [
    {"id": "wr-b1", "name": "Washroom – Block B, Floor 1", "type": "washroom", "sensitive": True,
     "cameras": ["cam-b1-corridor"]},
    {"id": "wr-a2", "name": "Washroom – Block A, Floor 2", "type": "washroom", "sensitive": True,
     "cameras": ["cam-a2-corridor"]},
    {"id": "wr-c0", "name": "Washroom – Block C, Ground", "type": "washroom", "sensitive": True,
     "cameras": ["cam-c0-corridor"], "cleaning": [{"start": "10:15", "end": "10:45"}, {"start": "13:30", "end": "14:00"}]},
    {"id": "st-east", "name": "East Stairwell", "type": "stairwell", "sensitive": False, "cameras": ["cam-st-east"]},
    {"id": "out-back", "name": "Behind Sports Hall", "type": "outdoor", "sensitive": False, "cameras": ["cam-yard-back"]},
    {"id": "bus-12", "name": "School Bus 12", "type": "bus", "sensitive": False, "cameras": ["cam-bus12-aisle"],
     "schedule": [{"start": "06:15", "end": "07:30", "type": "trip_to_school", "label": "Trip to school"},
                  {"start": "14:30", "end": "16:00", "type": "trip_home", "label": "Trip home"}]},
    {"id": "cr-boys", "name": "Boys' Changing Room", "type": "changing_room", "sensitive": True,
     "cameras": ["cam-gym-corridor"],
     "schedule": [{"start": "09:50", "end": "10:40", "type": "pe_period", "label": "PE – Grade 9"},
                  {"start": "12:30", "end": "13:20", "type": "pe_period", "label": "PE – Grade 10"}]},
    {"id": "canteen", "name": "Canteen", "type": "canteen", "sensitive": False, "cameras": ["cam-canteen"]},
]
