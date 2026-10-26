"""The SafeSound edge device runtime.

Inputs:  recogniser candidates (text, discarded immediately) and sensor readings.
Output:  a ``DeviceAlert`` - type, time, location, local risk and term ids.

Detections that happen close together (e.g. "hide it" + vape aerosol) are
merged into one alert by holding the first detection for ``hold_s`` seconds.
"""
from __future__ import annotations

import json
import logging
import urllib.request
from collections import deque
from datetime import datetime, timezone
from typing import Callable

from ..schemas import (MAX_ALERT_BYTES, DeviceAlert, KeywordCategory, KeywordHit, LocalRisk,
                       SensorHit, SensorKind)
from .keywords import KeywordSpotter, Term
from .sensor import AerosolDetector, Reading

log = logging.getLogger("safesound.edge")

Transport = Callable[[dict], bool]
DRUG = {KeywordCategory.drug_name, KeywordCategory.drug_slang}


class EdgeDevice:
    def __init__(self, device_id: str, location_id: str, spotter: KeywordSpotter,
                 transport: Transport, detector: AerosolDetector | None = None, hold_s: float = 30):
        self.device_id = device_id
        self.location_id = location_id
        self.spotter = spotter
        self.detector = detector or AerosolDetector()
        self.transport = transport
        self.hold_s = hold_s
        self._kw: dict[int, list] = {}            # term_id -> [category, count]
        self._sensor: SensorHit | None = None
        self._first_ts: float | None = None
        self.outbox: deque[dict] = deque(maxlen=500)  # retry queue when offline (alerts only)

    # ---- inputs -----------------------------------------------------------
    def on_utterance(self, text: str, ts: float) -> None:
        """Called by the recogniser. ``text`` is matched and then dropped."""
        hits = self.spotter.spot(text)
        del text
        for term in hits:
            entry = self._kw.setdefault(term.id, [term.category, 0])
            entry[1] += 1
        if hits:
            self._mark(ts)
        self.tick(ts)

    def on_reading(self, r: Reading) -> None:
        hit = self.detector.feed(r)
        if hit:
            self._add_sensor(hit)
            self._mark(r.ts - hit.duration_s)  # the alert time is when the aerosol event started
        self.tick(r.ts)

    def _add_sensor(self, hit: SensorHit) -> None:
        if self._sensor is None or hit.confidence > self._sensor.confidence:
            self._sensor = hit

    def _mark(self, ts: float) -> None:
        if self._first_ts is None:
            self._first_ts = ts

    # ---- output -----------------------------------------------------------
    def tick(self, now: float) -> None:
        if self._first_ts is None:
            return
        waited = now - self._first_ts
        # keep holding while an aerosol event is still developing, so "hide it" + vape become one alert
        if self.detector.event is not None and waited < self.hold_s + self.detector.MAX_EVENT_S:
            return
        if waited >= self.hold_s:
            self.flush(now, include_open_event=False)

    def flush(self, now: float, include_open_event: bool = True) -> dict | None:
        if include_open_event:
            hit = self.detector.flush(now)
            if hit:
                self._add_sensor(hit)
                self._mark(now - hit.duration_s)
        if self._first_ts is None:
            return None
        alert = self._build(self._first_ts)
        self._kw, self._sensor, self._first_ts = {}, None, None
        self._send(alert)
        return alert

    def _build(self, ts: float) -> dict:
        types: list[str] = []
        if self._kw:
            types.append("keyword")
        if self._sensor:
            types.append(self._sensor.kind.value)
        alert = DeviceAlert(
            device_id=self.device_id,
            ts=datetime.fromtimestamp(ts, tz=timezone.utc),
            location_id=self.location_id,
            types=types,
            local_risk=self._local_risk(),
            keywords=[KeywordHit(term_id=tid, category=cat, count=min(n, 50))
                      for tid, (cat, n) in self._kw.items()],
            sensor=self._sensor,
            keyword_list_version=self.spotter.version,
        )
        payload = json.loads(alert.model_dump_json())
        assert len(json.dumps(payload)) <= MAX_ALERT_BYTES
        return payload

    def _local_risk(self) -> LocalRisk:
        cats = {c for c, _ in self._kw.values()}
        s = self._sensor
        strong_air = s is not None and s.kind != SensorKind.aerosol_spike and s.confidence >= 0.7
        if strong_air or (cats & DRUG and KeywordCategory.deal_phrase in cats) or (s and cats):
            return LocalRisk.high
        if cats & DRUG or (s and s.kind != SensorKind.aerosol_spike) or len(cats) > 1:
            return LocalRisk.medium
        return LocalRisk.low

    def _send(self, alert: dict) -> None:
        self.outbox.append(alert)
        while self.outbox:
            if not self.transport(self.outbox[0]):
                log.warning("server unreachable, %d alert(s) queued", len(self.outbox))
                return
            self.outbox.popleft()

    def load_terms(self, terms: list[Term], version: int) -> None:
        self.spotter = KeywordSpotter(terms, version=version, fuzzy=self.spotter.fuzzy)


class HttpTransport:
    """Minimal stdlib client so the device image needs no extra packages."""

    def __init__(self, server: str, device_key: str, timeout: float = 5):
        self.server = server.rstrip("/")
        self.key = device_key
        self.timeout = timeout

    def _req(self, method: str, path: str, body: dict | None = None) -> dict:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.server + path, data=data, method=method, headers={
            "Content-Type": "application/json", "X-Device-Key": self.key})
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read() or b"{}")

    def __call__(self, alert: dict) -> bool:
        try:
            self._req("POST", "/api/device/alerts", alert)
            return True
        except Exception as exc:  # network down, server restarting...
            log.debug("send failed: %s", exc)
            return False

    def fetch_terms(self) -> tuple[list[Term], int]:
        cfg = self._req("GET", "/api/device/config")
        terms = [Term(id=t["id"], text=t["term"], category=KeywordCategory(t["category"]), lang=t["lang"])
                 for t in cfg["keywords"]]
        return terms, cfg["keyword_list_version"]
