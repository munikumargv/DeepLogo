"""Business logic: alert ingestion, fusion, routing, feedback learning, reports."""
from __future__ import annotations

import json
import logging
import threading
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from ..schemas import DeviceAlert, VideoCue, VideoEvent
from .auth import Auth, iso, now
from .context import resolve_context
from .db import DB
from .default_keywords import DEFAULT_KEYWORDS
from .fusion import FusionConfig, History, KeywordEvidence, fuse

log = logging.getLogger("safesound.server")

ROUTING = {
    "washroom": ["supervisor"], "stairwell": ["supervisor"], "changing_room": ["pe_teacher"],
    "bus": ["bus_attendant"], "outdoor": ["security"], "parking": ["security"], "canteen": ["supervisor"],
}
ROLE_ACTION = {"supervisor": "supervisor", "security": "security", "pe_teacher": "PE teacher",
               "bus_attendant": "bus attendant", "management": "school management", "counselor": "counselor"}
VIDEO_BEFORE = timedelta(minutes=15)
VIDEO_AFTER = timedelta(minutes=5)
DRUG = {"drug_name", "drug_slang"}


def parse_ts(s: str) -> datetime:
    return datetime.fromisoformat(s)


def feedback_factor(confirmed: int, false: int) -> float:
    """Beta(1,1) precision estimate mapped to a 0.5..1.5 multiplier (1.0 with no feedback)."""
    p = (confirmed + 1) / (confirmed + false + 2)
    return max(0.5, min(1.5, 0.5 + p))


class Engine:
    def __init__(self, db: DB, auth: Auth):
        self.db = db
        self.auth = auth

    # ---- setup -------------------------------------------------------------
    def create_school(self, school_id: str, name: str, timezone: str = "Asia/Dubai", settings: dict | None = None):
        self.db.insert("schools", id=school_id, name=name, timezone=timezone, settings=settings or {})
        for term, lang, cat, w in DEFAULT_KEYWORDS:
            self.db.insert("keywords", school_id=school_id, term=term, lang=lang, category=cat, weight=w,
                           created_at=iso(now()))

    def add_location(self, school_id: str, loc: dict) -> None:
        self.db.insert("locations", id=loc["id"], school_id=school_id, name=loc["name"], type=loc["type"],
                       sensitive=int(loc.get("sensitive", False)), cameras=loc.get("cameras", []),
                       schedule=loc.get("schedule", []), cleaning=loc.get("cleaning", []))

    def school(self, school_id: str) -> dict:
        return self.db.one("SELECT * FROM schools WHERE id=?", school_id)

    def fusion_config(self, school: dict) -> FusionConfig:
        cfg = FusionConfig.from_settings(school["settings"])
        if "alert_threshold" in school["settings"]:
            cfg.alert_threshold = int(school["settings"]["alert_threshold"])
        return cfg

    # ---- keywords ----------------------------------------------------------
    def keywords(self, school_id: str, enabled_only: bool = False) -> list[dict]:
        rows = self.db.all("SELECT * FROM keywords WHERE school_id=?" + (" AND enabled=1" if enabled_only else "")
                           + " ORDER BY category, lang, term", school_id)
        cfg = self.fusion_config(self.school(school_id))
        for r in rows:
            base = r["weight"] if r["weight"] is not None else cfg.category_weight[r["category"]]
            r["base_weight"] = round(base, 3)
            r["feedback_factor"] = round(feedback_factor(r["confirmed"], r["false_alarms"]), 2)
            r["effective_weight"] = round(min(0.95, base * r["feedback_factor"]), 3)
            r["suggest_review"] = r["false_alarms"] >= 3 and r["feedback_factor"] < 0.8
        return rows

    def bump_keyword_version(self, school_id: str) -> None:
        self.db.run("UPDATE schools SET keyword_version = keyword_version + 1 WHERE id=?", school_id)

    def device_config(self, device: dict) -> dict:
        school = self.school(device["school_id"])
        return {
            "device_id": device["id"], "location_id": device["location_id"],
            "keyword_list_version": school["keyword_version"],
            "keywords": [{"id": k["id"], "term": k["term"], "lang": k["lang"], "category": k["category"]}
                         for k in self.keywords(device["school_id"], enabled_only=True)],
        }

    # ---- ingestion ---------------------------------------------------------
    def ingest_video(self, school_id: str, ev: VideoEvent) -> list[int]:
        self.db.insert("video_events", school_id=school_id, camera_id=ev.camera_id, ts=iso(ev.ts),
                       cue=ev.cue.value, confidence=ev.confidence)
        # late-arriving video can raise (or lower) the score of a recent open incident
        rescored = []
        lo, hi = iso(ev.ts - VIDEO_AFTER), iso(ev.ts + VIDEO_BEFORE)
        for inc in self.db.all("SELECT i.id, l.cameras FROM incidents i JOIN locations l ON l.id=i.location_id "
                               "WHERE i.school_id=? AND i.ts BETWEEN ? AND ? AND i.status IN ('new','logged')",
                               school_id, lo, hi):
            if ev.camera_id in inc["cameras"]:
                self.rescore(inc["id"])
                rescored.append(inc["id"])
        return rescored

    def ingest_alert(self, device: dict, alert: DeviceAlert) -> dict:
        if alert.location_id != device["location_id"]:
            raise ValueError("location does not match the device's registered location")
        if alert.ts > now() + timedelta(minutes=5):
            raise ValueError("alert timestamp is in the future")
        school_id = device["school_id"]
        known = {k["id"] for k in self.db.all("SELECT id FROM keywords WHERE school_id=?", school_id)}
        if any(k.term_id not in known for k in alert.keywords):
            raise ValueError("unknown keyword id")
        self.db.run("UPDATE devices SET last_seen=?, keyword_list_version=? WHERE id=?",
                    iso(now()), alert.keyword_list_version, device["id"])
        alert_json = json.loads(alert.model_dump_json())
        inc_id = self.db.insert(
            "incidents", school_id=school_id, device_id=device["id"], location_id=alert.location_id,
            ts=iso(alert.ts), types=list(alert.types), alert=alert_json, context={}, score=0, level="low",
            action="", notified=0, routed_roles=[], fusion={}, status="logged", created_at=iso(now()))
        self.rescore(inc_id)
        return self.incident(inc_id)

    def _history(self, inc: dict, loc: dict, tz: ZoneInfo) -> History:
        kinds = set(inc["types"]) - {"aerosol_spike"}
        ts = parse_ts(inc["ts"])
        if not kinds:
            return History()
        h = History()
        local_min = lambda d: d.astimezone(tz).hour * 60 + d.astimezone(tz).minute
        here = local_min(ts)
        for p in self.db.all("SELECT ts, types FROM incidents WHERE location_id=? AND id<>? AND ts>=? AND ts<? "
                             "AND COALESCE(outcome,'')<>'false_alarm' AND status<>'suppressed'",
                             loc["id"], inc["id"], iso(ts - timedelta(days=7)), iso(ts)):
            if not kinds & set(p["types"]):
                continue
            if abs(local_min(parse_ts(p["ts"])) - here) <= 60:
                h.same_slot += 1
            else:
                h.other += 1
        return h

    def rescore(self, inc_id: int) -> dict:
        inc = self.db.one("SELECT * FROM incidents WHERE id=?", inc_id)
        school = self.school(inc["school_id"])
        loc = self.db.one("SELECT * FROM locations WHERE id=?", inc["location_id"])
        cfg = self.fusion_config(school)
        alert = DeviceAlert.model_validate(inc["alert"])
        ts = alert.ts
        tz = ZoneInfo(school["timezone"])

        kw_rows = {k["id"]: k for k in self.keywords(inc["school_id"])}
        kws = [KeywordEvidence(h.term_id, h.category, kw_rows[h.term_id]["effective_weight"], h.count,
                               kw_rows[h.term_id]["term"]) for h in alert.keywords if h.term_id in kw_rows]
        cues: dict[VideoCue, float] = {}
        if loc["cameras"]:
            q = ",".join("?" * len(loc["cameras"]))
            for v in self.db.all(f"SELECT cue, confidence FROM video_events WHERE camera_id IN ({q}) "
                                 "AND ts BETWEEN ? AND ?", *loc["cameras"], iso(ts - VIDEO_BEFORE), iso(ts + VIDEO_AFTER)):
                cue = VideoCue(v["cue"])
                cues[cue] = max(cues.get(cue, 0), v["confidence"])
        ctx = resolve_context(ts, school, loc)
        hist = self._history(inc, loc, tz)
        sensor_factor = feedback_factor(loc["sensor_confirmed"], loc["sensor_false"])
        res = fuse(kws, alert.sensor, list(cues.items()), ctx, hist, cfg, sensor_factor)

        roles: list[str] = []
        if res.alert:
            roles = list(ROUTING.get(loc["type"], ["supervisor"]))
            if res.level == "critical" and {k.category.value for k in kws} & DRUG and "security" not in roles:
                roles.append("security")
        escalate = res.pattern and res.alert
        if escalate:
            roles.append("management")
        if escalate:
            action = "Report to school management"
        elif res.alert:
            action = "Alert " + ROLE_ACTION[roles[0]]
        elif res.suppressed:
            action = "No alert"
        else:
            action = "Log only, no alert"

        status = inc["status"]
        if status in ("new", "logged", "suppressed"):
            status = "new" if roles else ("suppressed" if res.suppressed else "logged")
        context = {"period": ctx.period, "label": ctx.label, "describe": ctx.describe(),
                   "cleaning": ctx.cleaning, "local_time": ctx.local_time}
        self.db.run("UPDATE incidents SET context=?, score=?, level=?, action=?, routed_roles=?, fusion=?, status=? "
                    "WHERE id=?", context, res.score, res.level, action, roles, res.to_dict(), status, inc_id)
        new_roles = [r for r in roles if r not in inc["routed_roles"]]
        if new_roles:
            self.db.run("UPDATE incidents SET notified=1 WHERE id=?", inc_id)
            self._notify(inc["school_id"], inc_id, new_roles, res.level, action, loc, alert, ctx, res.score)
        return self.incident(inc_id)

    # ---- notifications -----------------------------------------------------
    def _notify(self, school_id, inc_id, roles, level, action, loc, alert, ctx, score):
        kinds = ", ".join(t.replace("_", " ") for t in alert.types)
        title = f"{level.upper()} · {loc['name']}"
        body = f"{kinds} · score {score} · {ctx.describe()} · {action}"
        hooks = self.school(school_id)["settings"].get("notify_webhooks", {})
        for role in roles:
            self.db.insert("notifications", school_id=school_id, incident_id=inc_id, role=role,
                           ts=iso(now()), title=title, body=body, read_by=[])
            if hooks.get(role):
                payload = {"incident_id": inc_id, "role": role, "title": title, "body": body, "level": level,
                           "location": loc["name"], "ts": iso(alert.ts)}
                threading.Thread(target=self._post_webhook, args=(hooks[role], payload), daemon=True).start()

    @staticmethod
    def _post_webhook(url: str, payload: dict) -> None:
        try:
            req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST",
                                         headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=5).close()
        except Exception as exc:
            log.warning("webhook %s failed: %s", url, exc)

    # ---- incidents ---------------------------------------------------------
    def incident(self, inc_id: int) -> dict | None:
        inc = self.db.one("SELECT i.*, l.name AS location_name, l.type AS location_type, l.sensitive "
                          "FROM incidents i JOIN locations l ON l.id=i.location_id WHERE i.id=?", inc_id)
        if inc and inc["alert"].get("keywords"):
            ids = [k["term_id"] for k in inc["alert"]["keywords"]]
            names = {r["id"]: r for r in self.db.all(
                f"SELECT id, term, lang FROM keywords WHERE id IN ({','.join('?' * len(ids))})", *ids)}
            for k in inc["alert"]["keywords"]:
                k["term"] = names.get(k["term_id"], {}).get("term", "(deleted term)")
        return inc

    def resolve(self, inc: dict, outcome: str, notes: str, user: dict) -> dict:
        """Close an incident with staff feedback; feedback tunes keyword weights and sensor trust."""
        alert = inc["alert"]
        with self.db.tx():
            self.db.run("UPDATE incidents SET status='resolved', outcome=?, notes=?, resolved_by=?, resolved_at=? "
                        "WHERE id=?", outcome, notes, user["display_name"], iso(now()), inc["id"])
            if outcome in ("confirmed", "false_alarm") and not inc.get("outcome"):
                col_k = "confirmed" if outcome == "confirmed" else "false_alarms"
                for k in alert.get("keywords", []):
                    self.db.run(f"UPDATE keywords SET {col_k} = {col_k} + 1 WHERE id=?", k["term_id"])
                if alert.get("sensor"):
                    col_s = "sensor_confirmed" if outcome == "confirmed" else "sensor_false"
                    self.db.run(f"UPDATE locations SET {col_s} = {col_s} + 1 WHERE id=?", inc["location_id"])
        if outcome == "confirmed":
            loc = self.db.one("SELECT * FROM locations WHERE id=?", inc["location_id"])
            self.db.insert("notifications", school_id=inc["school_id"], incident_id=inc["id"], role="counselor",
                           ts=iso(now()), title=f"Confirmed · {loc['name']}",
                           body=f"{', '.join(inc['types'])} confirmed by {user['display_name']} - "
                                "please follow up with student support / parents.", read_by=[])
        return self.incident(inc["id"])

    # ---- reports -----------------------------------------------------------
    def hotspots(self, school_id: str, days: int = 30) -> dict:
        school = self.school(school_id)
        tz = ZoneInfo(school["timezone"])
        since = now() - timedelta(days=days)
        rows = self.db.all("SELECT i.ts, i.types, i.level, i.status, i.outcome, i.location_id, l.name "
                           "FROM incidents i JOIN locations l ON l.id=i.location_id WHERE i.school_id=? AND i.ts>=? "
                           "AND i.status<>'suppressed' AND COALESCE(i.outcome,'')<>'false_alarm'",
                           school_id, iso(since))
        by_loc, by_type, by_hour, by_wd, by_level = Counter(), Counter(), Counter(), Counter(), Counter()
        matrix: dict[str, Counter] = defaultdict(Counter)
        daily = Counter()
        for r in rows:
            lt = parse_ts(r["ts"]).astimezone(tz)
            by_loc[r["name"]] += 1
            by_hour[lt.hour] += 1
            by_wd[lt.strftime("%a")] += 1
            by_level[r["level"]] += 1
            matrix[r["name"]][lt.hour] += 1
            daily[lt.date().isoformat()] += 1
            for t in r["types"]:
                by_type[t] += 1
        all_rows = self.db.all("SELECT outcome FROM incidents WHERE school_id=? AND ts>=? AND outcome IS NOT NULL",
                               school_id, iso(since))
        outcomes = Counter(r["outcome"] for r in all_rows)
        decided = outcomes["confirmed"] + outcomes["false_alarm"]
        return {
            "days": days, "total": len(rows),
            "by_location": by_loc.most_common(), "by_type": by_type.most_common(),
            "by_hour": [by_hour[h] for h in range(24)], "by_weekday": by_wd, "by_level": by_level,
            "matrix": {loc: [c[h] for h in range(24)] for loc, c in matrix.items()},
            "daily": sorted(daily.items()), "outcomes": outcomes,
            "precision": round(outcomes["confirmed"] / decided, 2) if decided else None,
        }

    def purge_expired(self, school_id: str) -> int:
        days = int(self.school(school_id)["settings"].get("retention_days", 180))
        cutoff = iso(now() - timedelta(days=days))
        n = self.db.one("SELECT COUNT(*) AS n FROM incidents WHERE school_id=? AND ts<?", school_id, cutoff)["n"]
        self.db.run("DELETE FROM notifications WHERE incident_id IN (SELECT id FROM incidents WHERE school_id=? AND ts<?)",
                    school_id, cutoff)
        self.db.run("DELETE FROM incidents WHERE school_id=? AND ts<?", school_id, cutoff)
        self.db.run("DELETE FROM video_events WHERE school_id=? AND ts<?", school_id,
                    iso(now() - timedelta(days=min(days, 30))))
        return n
