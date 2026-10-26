from datetime import datetime, timedelta, timezone

from conftest import login

from safesound.scenarios import SCENARIOS
from safesound.server.seed import demo_device_key

BY_ID = {s.id: s for s in SCENARIOS}
DEV = {"X-Device-Key": demo_device_key("wr-b1")}


def _alert(**over):
    a = {"device_id": "ss-wr-b1", "ts": datetime.now(timezone.utc).isoformat(), "location_id": "wr-b1",
         "types": ["vape"], "local_risk": "high",
         "sensor": {"kind": "vape", "confidence": 0.9, "duration_s": 60, "peak_pm2_5": 120, "peak_voc_index": 150}}
    a.update(over)
    return a


def test_device_alert_rejects_audio_transcripts_and_big_payloads(client):
    assert client.post("/api/device/alerts", json=_alert(), headers=DEV).status_code == 201
    r = client.post("/api/device/alerts", json=_alert(transcript="we are vaping"), headers=DEV)
    assert r.status_code == 422
    r = client.post("/api/device/alerts", json=_alert(audio="UklGR" * 2000), headers=DEV)
    assert r.status_code == 413
    assert client.post("/api/device/alerts", json=_alert(), headers={"X-Device-Key": "nope"}).status_code == 401
    # a device cannot report for another location
    assert client.post("/api/device/alerts", json=_alert(location_id="bus-12"), headers=DEV).status_code == 400
    assert client.post("/api/device/alerts", json=_alert(device_id="ss-bus-12"), headers=DEV).status_code == 403


def test_role_based_access(client, sim):
    bus = sim.run_scenario(BY_ID["vape_bus"])["incident_id"]
    wr = sim.run_scenario(BY_ID["vape_washroom"])["incident_id"]
    sup, att, cou = login(client, "supervisor"), login(client, "bus"), login(client, "counselor")
    ids = lambda h: {i["id"] for i in client.get("/api/incidents", headers=h).json()}
    assert ids(sup) == {wr} and ids(att) == {bus}
    assert client.get(f"/api/incidents/{bus}", headers=sup).status_code == 404
    assert {bus, wr} <= ids(cou)
    assert client.post(f"/api/incidents/{wr}/ack", headers=cou).status_code == 403  # counselor views only
    assert client.post("/api/keywords", json={"term": "zooted", "lang": "en", "category": "drug_slang"},
                       headers=sup).status_code == 403
    assert client.get("/api/incidents").status_code == 401
    notes = client.get("/api/notifications", headers=att).json()
    assert notes and all(n["role"] == "bus_attendant" for n in notes)


def test_audit_log_records_views_and_changes(client, sim):
    inc = sim.run_scenario(BY_ID["vape_washroom"])["incident_id"]
    sup = login(client, "supervisor")
    client.get(f"/api/incidents/{inc}", headers=sup)
    client.post(f"/api/incidents/{inc}/resolve", json={"outcome": "confirmed"}, headers=sup)
    client.post("/api/auth/login", json={"username": "admin", "password": "wrong"})
    log = client.get("/api/audit", headers=login(client, "principal")).json()
    actions = {(r["actor"], r["action"]) for r in log}
    assert {("supervisor", "view_incident"), ("supervisor", "resolve"), ("admin", "login_failed")} <= actions
    # confirmed incidents go to the counselor for follow-up
    assert any(n["incident_id"] == inc for n in client.get("/api/notifications", headers=login(client, "counselor")).json())


def test_keyword_update_reaches_devices(client):
    admin = login(client, "admin")
    v0 = client.get("/api/device/config", headers=DEV).json()["keyword_list_version"]
    r = client.post("/api/keywords", json={"term": "zooted", "lang": "en", "category": "drug_slang"}, headers=admin)
    assert r.status_code == 201
    cfg = client.get("/api/device/config", headers=DEV).json()
    assert cfg["keyword_list_version"] == v0 + 1 and any(k["term"] == "zooted" for k in cfg["keywords"])
    client.patch(f"/api/keywords/{r.json()['id']}", json={"enabled": False}, headers=admin)
    assert not any(k["term"] == "zooted" for k in client.get("/api/device/config", headers=DEV).json()["keywords"])


def test_feedback_lowers_weight_of_noisy_keyword(client):
    admin = login(client, "admin")
    kws = client.get("/api/keywords", headers=admin).json()["keywords"]
    how_much = next(k for k in kws if k["term"] == "how much")
    weed = next(k for k in kws if k["term"] == "weed")
    hdr = {"X-Device-Key": demo_device_key("canteen")}
    alert = {"device_id": "ss-canteen", "location_id": "canteen", "types": ["keyword"], "local_risk": "high",
             "keywords": [{"term_id": how_much["id"], "category": "deal_phrase"},
                          {"term_id": weed["id"], "category": "drug_slang"}]}
    scores = []
    for i in range(4):
        alert["ts"] = (datetime.now(timezone.utc) - timedelta(hours=i)).isoformat()
        inc = client.post("/api/device/alerts", json=alert, headers=hdr).json()
        scores.append(inc["score"])
        client.post(f"/api/incidents/{inc['incident_id']}/resolve", json={"outcome": "false_alarm"}, headers=admin)
    assert scores[-1] < scores[0]
    kw = next(k for k in client.get("/api/keywords", headers=admin).json()["keywords"] if k["term"] == "how much")
    assert kw["false_alarms"] == 4 and kw["effective_weight"] < kw["base_weight"] and kw["suggest_review"]


def test_late_video_rescoring_and_vms_auth(client, sim):
    res = sim.run_scenario(BY_ID["vape_changing_room"])
    assert res["response"]["score"] > 84 - 5
    r = client.post("/api/vms/events", json=[], headers={"X-VMS-Key": "bad"})
    assert r.status_code == 401
    r = client.post("/api/vms/events", json=[{"camera_id": "cam-unknown", "ts": datetime.now(timezone.utc).isoformat(),
                                               "cue": "gathering"}], headers={"X-VMS-Key": "demo-vms-key"})
    assert r.status_code == 400


def test_hotspots_and_retention(client, sim):
    sim.background(20)
    for s in SCENARIOS:
        sim.run_scenario(s)
    principal = login(client, "principal")
    rep = client.get("/api/reports/hotspots?days=30", headers=principal).json()
    assert rep["total"] >= 20 and rep["matrix"] and sum(rep["by_hour"]) == rep["total"]
    assert client.get("/api/reports/hotspots", headers=login(client, "supervisor")).status_code == 403
    admin = login(client, "admin")
    assert client.patch("/api/settings", json={"retention_days": 7}, headers=admin).status_code == 200
    engine = client.app.state.engine
    assert engine.purge_expired("demo") > 0
    left = client.get("/api/incidents?include_suppressed=true&limit=500", headers=admin).json()
    oldest = min(datetime.fromisoformat(i["ts"]) for i in left)
    assert oldest > datetime.now(timezone.utc) - timedelta(days=7, minutes=1)
