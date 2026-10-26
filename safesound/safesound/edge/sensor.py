"""Smoke / vape aerosol classification from an air-quality sensor.

Designed for a Sensirion SEN5x-class module (PM1/PM2.5/PM10, VOC index,
NOx index, RH, T) sampled about once per second.

Signatures used:
* vape  - very fine particles (PM1 dominates), humidity rises (PG/VG vapour),
          no combustion (NOx index stays flat).
* smoke - PM2.5 rise together with combustion gases (NOx, VOC).
* aerosol_spike - VOC jump with only a small particle rise (air freshener,
          deodorant, cleaning spray). Reported as low confidence so the
          server can log it and explain it with context.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..schemas import SensorHit, SensorKind


@dataclass
class Reading:
    ts: float          # seconds (monotonic or epoch)
    pm1: float
    pm2_5: float
    pm10: float
    voc_index: float   # 1..500, 100 = baseline
    nox_index: float   # 1..500, 1 = baseline
    rh: float          # %


def _ramp(x: float, lo: float, hi: float) -> float:
    if x <= lo:
        return 0.0
    if x >= hi:
        return 1.0
    return (x - lo) / (hi - lo)


@dataclass
class _Event:
    start: float
    peak: dict = field(default_factory=dict)
    quiet_since: float | None = None


class AerosolDetector:
    """Streaming detector: feed readings, get a SensorHit when an event ends."""

    START_PM = 8.0     # ug/m3 above baseline
    START_VOC = 40.0   # VOC index points above baseline
    END_QUIET_S = 20.0
    MAX_EVENT_S = 180.0
    MIN_CONFIDENCE = 0.3

    def __init__(self, alpha: float = 0.02):
        self.alpha = alpha
        self.base: dict | None = None
        self.event: _Event | None = None

    def _deltas(self, r: Reading) -> dict:
        b = self.base
        return {
            "pm1": r.pm1 - b["pm1"], "pm2_5": r.pm2_5 - b["pm2_5"], "pm10": r.pm10 - b["pm10"],
            "voc": r.voc_index - b["voc"], "nox": r.nox_index - b["nox"], "rh": r.rh - b["rh"],
            "abs_pm2_5": r.pm2_5, "abs_voc": r.voc_index,
        }

    def feed(self, r: Reading) -> SensorHit | None:
        if self.base is None:
            self.base = {"pm1": r.pm1, "pm2_5": r.pm2_5, "pm10": r.pm10,
                         "voc": r.voc_index, "nox": r.nox_index, "rh": r.rh}
            return None
        d = self._deltas(r)
        active = d["pm2_5"] > self.START_PM or d["pm1"] > self.START_PM or d["voc"] > self.START_VOC

        if self.event is None:
            if active:
                self.event = _Event(start=r.ts, peak=dict(d))
                return None
            # baseline only adapts outside events
            for k, v in (("pm1", r.pm1), ("pm2_5", r.pm2_5), ("pm10", r.pm10),
                         ("voc", r.voc_index), ("nox", r.nox_index), ("rh", r.rh)):
                self.base[k] += self.alpha * (v - self.base[k])
            return None

        ev = self.event
        for k, v in d.items():
            ev.peak[k] = max(ev.peak.get(k, v), v)
        if active:
            ev.quiet_since = None
        elif ev.quiet_since is None:
            ev.quiet_since = r.ts
        ended = (ev.quiet_since is not None and r.ts - ev.quiet_since >= self.END_QUIET_S) \
            or r.ts - ev.start >= self.MAX_EVENT_S
        if not ended:
            return None
        self.event = None
        return self.classify(ev.peak, int(r.ts - ev.start))

    def flush(self, now: float) -> SensorHit | None:
        if self.event is None:
            return None
        ev, self.event = self.event, None
        return self.classify(ev.peak, int(now - ev.start))

    @classmethod
    def classify(cls, p: dict, duration_s: int) -> SensorHit | None:
        pm_rise = max(p["pm1"], p["pm2_5"])
        fine_ratio = p["pm1"] / max(p["pm10"], 1.0)
        vape = (0.35 * _ramp(p["pm1"], 15, 120) + 0.2 * _ramp(fine_ratio, 0.6, 0.9)
                + 0.2 * _ramp(p["rh"], 0.5, 4) + 0.25 * (1 - _ramp(p["nox"], 10, 60)))
        smoke = (0.35 * _ramp(p["pm2_5"], 15, 120) + 0.35 * _ramp(p["nox"], 10, 60)
                 + 0.2 * _ramp(p["voc"], 40, 200) + 0.1 * (1 - _ramp(p["rh"], 1, 4)))
        spike = _ramp(p["voc"], 40, 200) * (1 - _ramp(pm_rise, 20, 60))

        if pm_rise < 20:  # too few particles for vape / smoke
            kind, conf = SensorKind.aerosol_spike, spike
        elif vape >= smoke:
            kind, conf = SensorKind.vape, vape
        else:
            kind, conf = SensorKind.smoke, smoke
        if conf < cls.MIN_CONFIDENCE:
            return None
        return SensorHit(kind=kind, confidence=round(conf, 2), duration_s=min(duration_s, 3600),
                         peak_pm2_5=round(max(p["abs_pm2_5"], 0), 1),
                         peak_voc_index=round(min(max(p["abs_voc"], 0), 500), 1))
