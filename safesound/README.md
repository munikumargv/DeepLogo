# SafeSound AI — privacy-first drug, smoke and vape detection for schools

SafeSound watches the places cameras can't go (washrooms, changing rooms, stairwells, buses, the back
of buildings). A small edge unit listens only for a school-configured list of drug words and slang, and
it senses cigarette smoke and vape aerosol. **No audio is streamed, recorded or stored.** Only a tiny
alert (type, time, location, risk level, matched term ids) leaves the device. The server fuses that
alert with video cues from cameras *outside* the area, the school timetable and recent history into
one **Fusion Score (0–100)**, then routes it to the right staff member.

```
 ┌──────────── edge unit (inside washroom) ─────────────┐         ┌──────────── SafeSound server ─────────────┐
 │ mic → RAM ring buffer → closed-grammar recogniser ──┐ │  alert  │ validate (strict schema, ≤4 KB)          │
 │       (never written, never sent)   keyword spotter ├─┼────────►│ + VMS / AI Cell cues (outside cameras)    │
 │ SEN5x PM/VOC/NOx/RH → aerosol classifier ───────────┘ │ ~300 B  │ + timetable context + 7-day history       │
 │ (vape / smoke / aerosol spike)                        │         │ → Fusion Score → route → dashboard / app │
 └───────────────────────────────────────────────────────┘         │ ← staff feedback tunes weights & trust    │
                                                                   └───────────────────────────────────────────┘
```

## Quick start

```bash
cd safesound
pip install -r requirements.txt
python -m safesound.server --port 8000            # seeds a demo school
python -m safesound.simulator --server http://localhost:8000 --background 60
# open http://localhost:8000  (users: admin, principal, supervisor, security, pe, bus, counselor / safesound123)
python -m pytest -q tests
```

The simulator runs the **real device code** (keyword spotter, aerosol classifier, alert builder) on
synthetic sensor traces and phrases, posts VMS camera cues, and prints the result next to the proposal:

| Scenario | Score | Level | Action | Proposal |
|---|---|---|---|---|
| Vaping in washroom | 88 | High | Alert supervisor | 88 |
| Drug deal behind building | 90 | Critical | Alert security | 90 |
| Smoking in stairwell | 85 | High | Alert supervisor | 85 |
| Vaping on school bus | 78 | High | Alert bus attendant | 80 |
| Vaping in changing room | 86 | High | Alert PE teacher | 84 |
| Drug talk in canteen | 35 | Low | Log only, no alert | 35 |
| Repeated alerts in same washroom | 93 | Critical | Report to school management | 92 |
| Air freshener spray | 15 | Low | No alert | 15 |

The simulator's scores depend on the history already in the database. Start from a fresh DB (delete
`safesound.db`) to reproduce the table exactly.

## Hosting on GitHub

**GitHub Pages: public demo of the dashboard.** The workflow `.github/workflows/safesound-pages.yml`
runs the tests, runs the simulator during the build, and publishes a serverless snapshot of the
dashboard: `python -m safesound.static_export _site`. There, every demo account sees its own
role-filtered data. Acknowledging, resolving and keyword edits work, but only in that browser tab.
One-time setup: **Settings → Pages → Source: GitHub Actions**. After that, every push to `master`
redeploys the demo to `https://<owner>.github.io/<repo>/`.

**GitHub Codespaces: the full live system.** Choose **Code → Codespaces → Create codespace**. The
dev container installs the dependencies, starts the server on port 8000, fills it with simulated
data, and opens the dashboard. Run more scenarios with
`cd safesound && python -m safesound.simulator --scenario vape_bus`.

For a permanent live deployment (real devices posting alerts), run `python -m safesound.server --host 0.0.0.0`
on the school's on-prem server or AI Cell, behind HTTPS.

## Layout

| Path | What it does |
|---|---|
| `safesound/edge/keywords.py` | English + Arabic normalisation (diacritics, alef/ya/ta-marbuta, clitics), phrase matching and fuzzy matching against the school list |
| `safesound/edge/sensor.py` | Streaming aerosol event detector. Vape = fine PM1 + humidity rise + no NOx; smoke = PM2.5 + NOx/VOC; a VOC jump with few particles = aerosol spike (air freshener, deodorant) |
| `safesound/edge/device.py` | Device runtime: merges nearby detections into one alert, computes local risk, keeps an offline retry queue of alerts only, stdlib HTTP client |
| `safesound/edge/audio.py` | Real-hardware microphone front-end (optional `vosk` + `sounddevice`): RAM-only ring buffer, recogniser grammar restricted to the configured terms + `[unk]` |
| `safesound/schemas.py` | Wire formats. Every model uses `extra="forbid"`, so a transcript or audio field is rejected |
| `safesound/server/fusion.py` | Fusion Score, with a factor-by-factor explanation |
| `safesound/server/context.py` | Timetable → class / break / lunch / after school; location overrides (PE periods, bus trips); cleaning schedules |
| `safesound/server/engine.py` | Ingestion, VMS cue lookup, re-scoring on late video, routing, notifications and webhooks, feedback learning, hotspot reports, retention purge |
| `safesound/server/auth.py` | Roles and permissions, PBKDF2 passwords, session tokens, hashed device keys, audit log |
| `safesound/server/app.py` | FastAPI app (API docs at `/docs`) |
| `safesound/server/static/index.html` | Staff dashboard. Responsive, so it doubles as the mobile app, with desktop notifications |
| `safesound/scenarios.py`, `safesound/simulator.py` | Reference scenarios, synthetic sensor traces, replay CLI |
| `safesound/static_export.py` | Builds the serverless GitHub Pages demo |

## Fusion Score

Each evidence source is turned into a probability-like value and combined with a noisy-OR. Context and
history then add or subtract points:

```
K = noisy-OR(term weights)  (+0.40 if a drug word and a deal phrase occur together, +0.20 for drug + hiding)
S = sensor confidence × type weight (vape 0.72, smoke 0.80, spike 0.35) × location trust
    × 0.5 during a cleaning slot × 0.6 if a cleaner was seen entering
V = noisy-OR(video cues: hand exchange 0.40, long dwell 0.25, fast exit 0.25, group entry 0.20 …)
score = 100·(1 − (1−K)(1−S)(1−V)) + context (class +6, after school +4, PE +2, lunch −5 …)
        + history (5 per same-slot repeat in 7 days, 2 per other repeat, max 20)
```

* **Levels:** Low < 40 ≤ Medium < 70 ≤ High < 90 ≤ Critical.
* **Thresholds:** alerts go out at score ≥ 60 (configurable per school). Below 20 the event is suppressed; in between it is logged only.
* **Routing:** washroom/stairwell/canteen → supervisor, changing room → PE teacher, bus → bus attendant, outdoor/parking → security. A critical drug-keyword alert adds security.
* **Escalation:** 4 or more detections in the same slot within a week → report to school management.
* **Every weight is in `FusionConfig`** and can be overridden per school via `settings.fusion`.

**Feedback loop.** When staff close an incident as *confirmed* or *false alarm*, the outcome updates
per-term and per-location counts. These map to a 0.5–1.5× multiplier (Beta-prior precision), so a
term or sensor that keeps causing false alarms loses weight automatically. A term with repeated false
alarms is flagged for review in the Keywords screen. Confirmed incidents notify the counselor so
student support and parents can be involved.

## Privacy and compliance (UAE PDPL)

* No audio leaves the device or touches disk. The recogniser can only output listed terms.
* The alert contains term **ids**, not words heard around them. The server rejects unknown fields and payloads over 4 KB.
* Each device key is bound to one location, so a device cannot report for another place.
* Locations marked `sensitive` have no cameras inside. Only entrance and corridor cameras are linked.
* Role-based access: responders see only incidents routed to their role; counselors and management have read-only overviews.
* The audit log records sign-ins, failed sign-ins, every incident view, responses, permission denials and configuration changes.
* Retention: incidents are purged after `retention_days` (default 180) and video cues after 30 days.
* No student identities are stored anywhere in the system.

## Integrations

* **Edge devices:** `GET /api/device/config` (keyword list + version), `POST /api/device/alerts`, `POST /api/device/heartbeat`, all with header `X-Device-Key`.
* **VMS / AI Cell:** `POST /api/vms/events` with header `X-VMS-Key`, taking `[{camera_id, ts, cue, confidence}]`. Cues that arrive after an alert re-score it.
* **Mobile push / SMS:** per-role webhooks in Settings (`notify_webhooks`) receive a JSON payload for every routed alert.

## Extending to hospitals, prisons, malls, warehouses

The device and the Fusion Score are domain-neutral. To add a new domain, add location types to
`ROUTING` (for example `oxygen_zone → nursing_supervisor`), load a domain keyword list (theft, threat,
aggression), add a role, and set a timetable (visiting hours, shifts).
