import json

from safesound.edge.device import EdgeDevice
from safesound.edge.keywords import KeywordSpotter, Term, normalize
from safesound.edge.sensor import AerosolDetector
from safesound.scenarios import sensor_trace
from safesound.schemas import KeywordCategory as C

TERMS = [Term(1, "hide it", C.hiding_phrase), Term(2, "weed", C.drug_slang), Term(3, "how much", C.deal_phrase),
         Term(4, "حشيش", C.drug_name, "ar"), Term(5, "المدرس جاي", C.hiding_phrase, "ar"),
         Term(6, "captagon", C.drug_name), Term(7, "hide", C.hiding_phrase)]


def test_arabic_normalization():
    assert normalize("المُدَرِّسُ جايّ!") == normalize("المدرس جاي")
    assert normalize("أحمد") == "احمد"


def test_spotter_matches_phrases_arabic_and_fuzzy():
    sp = KeywordSpotter(TERMS)
    assert [t.id for t in sp.spot("quick, HIDE IT!")] == [1]          # longest phrase wins over "hide"
    assert {t.id for t in sp.spot("how much for the weed")} == {2, 3}
    assert [t.id for t in sp.spot("والحشيش")] == [4]                  # clitic prefix
    assert [t.id for t in sp.spot("انتبه المدرسُ جاي")] == [5]
    assert [t.id for t in sp.spot("kaptagon")] == [6]                 # fuzzy for longer words
    assert sp.spot("see you at the library") == []


def _classify(profile):
    d, hit = AerosolDetector(), None
    for r in sensor_trace(profile, 0):
        hit = d.feed(r) or hit
    return hit or d.flush(1e9)


def test_sensor_classification():
    assert _classify("vape").kind.value == "vape"
    assert _classify("smoke").kind.value == "smoke"
    spike = _classify("air_freshener")
    assert spike.kind.value == "aerosol_spike" and spike.peak_pm2_5 < 30
    assert _classify("clean") is None


def test_device_sends_only_minimal_alert_and_merges_detections():
    sent = []
    dev = EdgeDevice("dev-1", "wr-1", KeywordSpotter(TERMS, version=3), lambda a: sent.append(a) or True)
    secret_sentence = "my name is Sara and we should hide it before class"
    trace = sensor_trace("vape", 1_000_000)
    for r in trace:
        if r.ts == trace[65].ts:
            dev.on_utterance(secret_sentence, r.ts)
        dev.on_reading(r)
    dev.flush(trace[-1].ts)
    assert len(sent) == 1, "keyword and vape within the hold window become one alert"
    a = sent[0]
    assert set(a["types"]) == {"keyword", "vape"} and a["local_risk"] == "high"
    assert a["keywords"] == [{"term_id": 1, "category": "hiding_phrase", "count": 1}]
    blob = json.dumps(a)
    for word in ("Sara", "name", "class", "hide"):
        assert word not in blob
    assert len(blob) < 600


def test_device_queues_alerts_when_offline():
    up = {"ok": False}
    sent = []
    dev = EdgeDevice("dev-1", "wr-1", KeywordSpotter(TERMS), lambda a: up["ok"] and (sent.append(a) or True))
    dev.on_utterance("weed", 0); dev.flush(1)
    dev.on_utterance("how much", 100); dev.flush(101)
    assert len(dev.outbox) == 2 and not sent
    up["ok"] = True
    dev.on_utterance("captagon", 200); dev.flush(201)
    assert len(sent) == 3 and not dev.outbox
