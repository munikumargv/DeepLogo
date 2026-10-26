"""Replay the reference scenarios (and optional background activity) through
real edge-device code against a SafeSound server.

    python -m safesound.simulator --server http://localhost:8000
    python -m safesound.simulator --server http://localhost:8000 --background 60
"""
from __future__ import annotations

import argparse
import json
import random
import urllib.request
from datetime import date, datetime, timedelta, timezone
from typing import Callable
from zoneinfo import ZoneInfo

from .edge.device import EdgeDevice
from .edge.keywords import KeywordCategory, KeywordSpotter, Term
from .scenarios import DEMO_LOCATIONS, SCENARIOS, Scenario, sensor_trace
from .server.seed import DEMO_VMS_KEY, demo_device_key

PostJSON = Callable[[str, dict | list, dict], dict]  # (path, body, headers) -> response json
TZ = ZoneInfo("Asia/Dubai")


def http_poster(server: str) -> PostJSON:
    def post(path, body, headers):
        req = urllib.request.Request(server.rstrip("/") + path, data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json", **headers})
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())
    return post


def http_getter(server: str):
    def get(path, headers):
        req = urllib.request.Request(server.rstrip("/") + path, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())
    return get


def last_weekday(weekday: int, today: date | None = None) -> date:
    today = today or datetime.now(TZ).date()
    d = today - timedelta(days=1)
    while d.weekday() != weekday:
        d -= timedelta(days=1)
    return d


def local_dt(d: date, hhmm: str) -> datetime:
    h, m = map(int, hhmm.split(":"))
    return datetime(d.year, d.month, d.day, h, m, tzinfo=TZ)


class Simulator:
    def __init__(self, post: PostJSON, get, vms_key: str = DEMO_VMS_KEY):
        self.post, self.get, self.vms_key = post, get, vms_key
        self.spotters: dict[str, KeywordSpotter] = {}

    def _spotter(self, location_id: str) -> KeywordSpotter:
        if location_id not in self.spotters:  # device pulls the school's keyword list
            cfg = self.get("/api/device/config", {"X-Device-Key": demo_device_key(location_id)})
            terms = [Term(t["id"], t["term"], KeywordCategory(t["category"]), t["lang"]) for t in cfg["keywords"]]
            self.spotters[location_id] = KeywordSpotter(terms, cfg["keyword_list_version"])
        return self.spotters[location_id]

    def run_device(self, location_id: str, at: datetime, sensor: str | None, utterances: list[str],
                   seed: int = 7) -> list[dict]:
        responses: list[dict] = []
        key = demo_device_key(location_id)

        def transport(alert: dict) -> bool:
            responses.append(self.post("/api/device/alerts", alert, {"X-Device-Key": key}))
            return True

        dev = EdgeDevice(f"ss-{location_id}", location_id, self._spotter(location_id), transport)
        t0 = at.timestamp()
        timeline: list[tuple[float, object]] = [(t0 + 5 + 10 * i, u) for i, u in enumerate(utterances)]
        if sensor:
            timeline += [(r.ts, r) for r in sensor_trace(sensor, t0 - 60, seed=seed)]
        timeline.sort(key=lambda x: x[0])
        for ts, item in timeline:
            if isinstance(item, str):
                dev.on_utterance(item, ts)
            else:
                dev.on_reading(item)
        dev.flush(timeline[-1][0] if timeline else t0)
        return responses

    def video(self, location_id: str, at: datetime, cues: list[tuple[str, int]]) -> list[dict]:
        cam = next(l for l in DEMO_LOCATIONS if l["id"] == location_id)["cameras"][0]
        if not cues:
            return []
        return self.post("/api/vms/events", [{"camera_id": cam, "cue": c, "confidence": 0.9,
                                           "ts": (at + timedelta(minutes=m)).astimezone(timezone.utc).isoformat()}
                                          for c, m in cues], {"X-VMS-Key": self.vms_key})["rescored_incidents"]

    def run_scenario(self, s: Scenario, day: date | None = None) -> dict:
        day = day or last_weekday(s.weekday)
        at = local_dt(day, s.local_time)
        for i, (days_before, hhmm) in enumerate(s.priors):
            self.run_device(s.location_id, local_dt(day - timedelta(days=days_before), hhmm), s.sensor, [], seed=100 + i)
        self.video(s.location_id, at, [c for c in s.cues if c[1] <= 0])
        res = self.run_device(s.location_id, at, s.sensor, s.utterances)
        inc = {"id": res[-1]["incident_id"], **res[-1]}
        # late video (e.g. group exits fast) arrives after the alert -> the server re-scores
        for r in self.video(s.location_id, at, [c for c in s.cues if c[1] > 0]):
            if r["id"] == inc["id"]:
                inc.update(r)
        return {"scenario": s, "incident_id": inc["id"], "response": inc}

    def background(self, n: int, seed: int = 1) -> int:
        """Past activity for hotspot reports: a few hotspots, mostly vape, 15-29 days ago."""
        rnd = random.Random(seed)
        today = datetime.now(TZ).date()
        weighted = [("wr-b1", "10:2"), ("wr-b1", "10:3"), ("wr-b1", "13:1"), ("st-east", "09:3"),
                    ("cr-boys", "10:1"), ("bus-12", "15:0"), ("wr-a2", "11:0"), ("wr-c0", "12:4")]
        sent = 0
        for _ in range(n):
            loc, hm = rnd.choice(weighted)
            d = today - timedelta(days=rnd.randint(15, 29))  # outside the 7-day history window of the scenarios
            while d.weekday() > 4:
                d = today - timedelta(days=rnd.randint(15, 29))
            sensor = rnd.choices(["vape", "smoke", "air_freshener"], [6, 2, 2])[0]
            if loc == "bus-12":
                sensor = "vape"
            self.run_device(loc, local_dt(d, f"{hm}{rnd.randint(0, 9)}"), sensor, [], seed=rnd.randint(0, 9999))
            sent += 1
        return sent


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--server", default="http://localhost:8000")
    ap.add_argument("--scenario", default="all", help="scenario id or 'all'")
    ap.add_argument("--background", type=int, default=0, help="also generate N past events for reports")
    args = ap.parse_args()
    sim = Simulator(http_poster(args.server), http_getter(args.server))
    if args.background:
        print(f"background: sent {sim.background(args.background)} past events")
    chosen = [s for s in SCENARIOS if args.scenario in ("all", s.id)]
    print(f"{'Scenario':34} {'Score':>5}  {'Level':9} {'Action':28} (proposal)")
    for s in chosen:
        r = sim.run_scenario(s)
        inc = r["response"]
        print(f"{s.title:34} {inc['score']:>5}  {inc['level']:9} {inc['action']:28} ({s.expected_score} {s.expected_action})")


if __name__ == "__main__":
    main()
