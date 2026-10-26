"""Build a static, serverless demo of the dashboard (for GitHub Pages).

Runs the demo school and the simulator in-process, then saves what each demo
user would see from the API as JSON. The dashboard detects the static build
and answers its API calls from those snapshots in the browser.

    python -m safesound.static_export _site
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from .scenarios import SCENARIOS
from .server.app import STATIC, create_app
from .server.seed import DEMO_PASSWORD, DEMO_USERS
from .simulator import Simulator


def build(out: Path) -> None:
    tmp = tempfile.mkdtemp()
    client = TestClient(create_app(str(Path(tmp) / "demo.db"), demo=True))

    def post(path, body, headers):
        r = client.post(path, json=body, headers=headers)
        r.raise_for_status()
        return r.json()

    def get_dev(path, headers):
        r = client.get(path, headers=headers)
        r.raise_for_status()
        return r.json()

    sim = Simulator(post, get_dev)
    sim.background(60)
    for s in SCENARIOS:
        sim.run_scenario(s)

    data = out / "demo-data"
    data.mkdir(parents=True, exist_ok=True)
    for username, _, _ in DEMO_USERS:
        token = post("/api/auth/login", {"username": username, "password": DEMO_PASSWORD}, {})["token"]
        h = {"Authorization": f"Bearer {token}"}

        def get(path):
            r = client.get(path, headers=h)
            return r.json() if r.status_code == 200 else None

        me = get("/api/me")
        incidents = get("/api/incidents?include_suppressed=true&limit=500")
        snap = {
            "me": me, "incidents": incidents,
            "details": {i["id"]: get(f"/api/incidents/{i['id']}") for i in incidents},
            "notifications": get("/api/notifications"),
            "hotspots": {d: get(f"/api/reports/hotspots?days={d}") for d in (7, 30, 90)},
            "keywords": get("/api/keywords"), "devices": get("/api/devices"),
            "locations": get("/api/locations"), "settings": get("/api/settings"),
            "audit": get("/api/audit?limit=300"),
        }
        (data / f"{username}.json").write_text(json.dumps(snap, ensure_ascii=False))

    html = (STATIC / "index.html").read_text()
    html = html.replace("<script>\nconst $", "<script>window.SAFESOUND_STATIC = true;</script>\n<script>\nconst $", 1)
    (out / "index.html").write_text(html)
    (out / ".nojekyll").write_text("")
    shutil.rmtree(tmp, ignore_errors=True)
    print(f"static demo written to {out}")


if __name__ == "__main__":
    build(Path(sys.argv[1] if len(sys.argv) > 1 else "_site"))
