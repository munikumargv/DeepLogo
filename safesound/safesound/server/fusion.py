"""Fusion Score: keyword + air sensor + video + context + history -> 0..100.

Evidence sources are combined with a noisy-OR (each source is an independent
chance that something is really happening), then context and history add or
remove points. Every step is recorded so staff can see *why* a score is what
it is.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

from ..schemas import KeywordCategory, SensorHit, SensorKind, VideoCue
from .context import Context

DRUG = {KeywordCategory.drug_name, KeywordCategory.drug_slang}


@dataclass
class FusionConfig:
    category_weight: dict = field(default_factory=lambda: {
        "drug_name": 0.40, "drug_slang": 0.45, "deal_phrase": 0.25, "hiding_phrase": 0.25})
    combo_drug_deal: float = 0.40     # drug word + deal phrase together
    combo_drug_hiding: float = 0.20
    sensor_weight: dict = field(default_factory=lambda: {"vape": 0.72, "smoke": 0.80, "aerosol_spike": 0.35})
    cue_weight: dict = field(default_factory=lambda: {
        "group_entry": 0.20, "long_dwell": 0.25, "hand_exchange": 0.40, "small_group": 0.15,
        "gathering": 0.20, "fast_exit": 0.25, "back_seat": 0.25, "normal_activity": 0.0,
        "cleaner_entry": 0.0})
    cleaning_sensor_factor: float = 0.5   # cleaning schedule explains part of an aerosol reading
    cleaner_cue_sensor_factor: float = 0.6
    context_points: dict = field(default_factory=lambda: {
        "class": 6, "after_school": 4, "off_hours": 4, "weekend": 4, "pe_period": 2,
        "before_school": 0, "break": 0, "trip_home": 0, "trip_to_school": 0, "lunch": -5})
    history_same_slot_points: int = 5
    history_other_points: int = 2
    history_max_points: int = 20
    pattern_min_count: int = 4         # e.g. vape 4 times in a week in the same slot
    alert_threshold: int = 60
    suppress_below: int = 20

    @classmethod
    def from_settings(cls, settings: dict) -> "FusionConfig":
        cfg = cls()
        for k, v in (settings.get("fusion") or {}).items():
            if hasattr(cfg, k):
                cur = getattr(cfg, k)
                setattr(cfg, k, {**cur, **v} if isinstance(cur, dict) else v)
        return cfg


@dataclass
class KeywordEvidence:
    term_id: int
    category: KeywordCategory
    weight: float       # effective weight after feedback adjustment
    count: int = 1
    term: str = ""


@dataclass
class History:
    same_slot: int = 0  # same type, same location, +-60 min time of day, last 7 days
    other: int = 0      # same type, same location, other times, last 7 days


@dataclass
class FusionResult:
    score: int
    level: str
    alert: bool
    suppressed: bool
    pattern: bool
    components: dict
    factors: list

    def to_dict(self) -> dict:
        return asdict(self)


def level_for(score: int) -> str:
    if score >= 90:
        return "critical"
    if score >= 70:
        return "high"
    if score >= 40:
        return "medium"
    return "low"


def _noisy_or(ps) -> float:
    q = 1.0
    for p in ps:
        q *= 1 - max(0.0, min(1.0, p))
    return 1 - q


def fuse(keywords: list[KeywordEvidence], sensor: SensorHit | None, cues: list[tuple[VideoCue, float]],
         ctx: Context, history: History, cfg: FusionConfig, sensor_factor: float = 1.0) -> FusionResult:
    factors: list[dict] = []

    # --- keywords ---
    kw_ps = []
    for k in keywords:
        reps = 1 + 0.5 * (min(k.count, 3) - 1)
        p = 1 - (1 - k.weight) ** reps
        kw_ps.append(p)
        factors.append({"source": "keyword", "label": f"{k.category.value.replace('_', ' ')}"
                        + (f" “{k.term}”" if k.term else "") + (f" ×{k.count}" if k.count > 1 else ""),
                        "value": round(p, 2)})
    cats = {k.category for k in keywords}
    if cats & DRUG and KeywordCategory.deal_phrase in cats:
        kw_ps.append(cfg.combo_drug_deal)
        factors.append({"source": "keyword", "label": "drug word + deal phrase together", "value": cfg.combo_drug_deal})
    if cats & DRUG and KeywordCategory.hiding_phrase in cats:
        kw_ps.append(cfg.combo_drug_hiding)
        factors.append({"source": "keyword", "label": "drug word + hiding phrase together", "value": cfg.combo_drug_hiding})
    K = _noisy_or(kw_ps)

    # --- air sensor ---
    S = 0.0
    if sensor:
        S = sensor.confidence * cfg.sensor_weight[sensor.kind.value] * sensor_factor
        factors.append({"source": "sensor", "label": f"{sensor.kind.value.replace('_', ' ')} detected "
                        f"(confidence {sensor.confidence:.0%})", "value": round(S, 2)})
        if sensor_factor != 1.0:
            factors.append({"source": "feedback", "label": f"location sensor reliability ×{sensor_factor:.2f}",
                            "value": None})
        if ctx.cleaning:
            S *= cfg.cleaning_sensor_factor
            factors.append({"source": "context", "label": f"cleaning schedule (sensor ×{cfg.cleaning_sensor_factor})",
                            "value": None})
        if any(c == VideoCue.cleaner_entry for c, _ in cues):
            S *= cfg.cleaner_cue_sensor_factor
            factors.append({"source": "video", "label": f"cleaner seen entering (sensor ×{cfg.cleaner_cue_sensor_factor})",
                            "value": None})

    # --- video (cameras outside the sensitive area) ---
    v_ps = []
    for cue, conf in {c: conf for c, conf in cues}.items():
        w = cfg.cue_weight.get(cue.value, 0.0)
        if w:
            v_ps.append(w * conf)
            factors.append({"source": "video", "label": cue.value.replace("_", " "), "value": round(w * conf, 2)})
    V = _noisy_or(v_ps)

    evidence = 100 * _noisy_or([K, S, V])

    # --- context & history ---
    ctx_pts = cfg.context_points.get(ctx.period, 0)
    if ctx_pts:
        factors.append({"source": "context", "label": ctx.describe(), "points": ctx_pts})
    hist_pts = min(cfg.history_max_points,
                   history.same_slot * cfg.history_same_slot_points + history.other * cfg.history_other_points)
    if hist_pts:
        factors.append({"source": "history", "label": f"{history.same_slot + history.other} earlier detection(s) here "
                        f"in 7 days ({history.same_slot} in same time slot)", "points": hist_pts})
    pattern = history.same_slot + 1 >= cfg.pattern_min_count
    if pattern:
        factors.append({"source": "history", "label": "repeated pattern - escalate to school management", "points": 0})

    score = int(round(max(0.0, min(100.0, evidence + ctx_pts + hist_pts))))
    return FusionResult(
        score=score, level=level_for(score),
        alert=score >= cfg.alert_threshold, suppressed=score < cfg.suppress_below, pattern=pattern,
        components={"keyword": round(K * 100), "sensor": round(S * 100), "video": round(V * 100),
                    "evidence": round(evidence), "context_points": ctx_pts, "history_points": hist_pts},
        factors=factors,
    )
