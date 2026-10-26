"""SafeSound AI server: device ingestion, VMS integration, dashboard API."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError

from ..schemas import MAX_ALERT_BYTES, DeviceAlert, KeywordCategory, VideoEvent
from .auth import PERMISSIONS, ROLES, Auth, can, iso, now
from .db import DB
from .engine import Engine

STATIC = Path(__file__).parent / "static"


class LoginIn(BaseModel):
    username: str
    password: str


class ResolveIn(BaseModel):
    outcome: Literal["confirmed", "false_alarm", "inconclusive"]
    notes: str = Field("", max_length=1000)


class KeywordIn(BaseModel):
    term: str = Field(min_length=2, max_length=60)
    lang: Literal["en", "ar"]
    category: KeywordCategory
    weight: float | None = Field(None, ge=0.05, le=0.9)


class KeywordPatch(BaseModel):
    enabled: bool | None = None
    weight: float | None = Field(None, ge=0.05, le=0.9)
    category: KeywordCategory | None = None


class DeviceIn(BaseModel):
    id: str = Field(min_length=2, max_length=64)
    location_id: str
    name: str = ""


class UserIn(BaseModel):
    username: str = Field(min_length=3, max_length=40)
    password: str = Field(min_length=8)
    role: str
    display_name: str


class SettingsPatch(BaseModel):
    alert_threshold: int | None = Field(None, ge=20, le=95)
    retention_days: int | None = Field(None, ge=7, le=730)
    notify_webhooks: dict[str, str] | None = None


def create_app(db_path: str | None = None, demo: bool | None = None) -> FastAPI:
    db = DB(db_path or os.environ.get("SAFESOUND_DB", "safesound.db"))
    auth = Auth(db)
    engine = Engine(db, auth)
    demo = demo if demo is not None else os.environ.get("SAFESOUND_DEMO", "1") == "1"
    if demo and not db.one("SELECT id FROM schools LIMIT 1"):
        from .seed import seed_demo
        seed_demo(engine, auth)
    for s in db.all("SELECT id FROM schools"):
        engine.purge_expired(s["id"])

    app = FastAPI(title="SafeSound AI", version="1.0",
                  description="Privacy-first drug, smoke and vape detection for schools")
    app.state.db, app.state.auth, app.state.engine = db, auth, engine

    # ---- dependencies ---------------------------------------------------------
    def current_user(authorization: str = Header("")) -> dict:
        token = authorization.removeprefix("Bearer ").strip()
        user = auth.user_for_token(token) if token else None
        if not user:
            raise HTTPException(401, "not signed in")
        return user

    def need(perm: str):
        def dep(user: dict = Depends(current_user)) -> dict:
            if not can(user["role"], perm):
                auth.audit(user["username"], "denied", perm, school_id=user["school_id"])
                raise HTTPException(403, "not allowed for your role")
            return user
        return dep

    def current_device(x_device_key: str = Header("")) -> dict:
        dev = auth.device_for_key(x_device_key) if x_device_key else None
        if not dev:
            raise HTTPException(401, "unknown device")
        return dev

    def visible(inc: dict | None, user: dict) -> dict:
        if not inc or inc["school_id"] != user["school_id"]:
            raise HTTPException(404, "incident not found")
        if not can(user["role"], "incidents:view_all") and user["role"] not in inc["routed_roles"]:
            raise HTTPException(404, "incident not found")
        return inc

    # ---- public -------------------------------------------------------------
    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/api/health")
    def health():
        return {"ok": True}

    # ---- auth ---------------------------------------------------------------
    @app.post("/api/auth/login")
    def login(body: LoginIn):
        res = auth.login(body.username, body.password)
        if not res:
            auth.audit(body.username, "login_failed")
            raise HTTPException(401, "wrong username or password")
        token, u = res
        auth.audit(u["username"], "login", school_id=u["school_id"])
        return {"token": token, "user": {k: u[k] for k in ("username", "display_name", "role", "school_id")}}

    @app.post("/api/auth/logout")
    def logout(authorization: str = Header(""), user: dict = Depends(current_user)):
        auth.logout(authorization.removeprefix("Bearer ").strip())
        auth.audit(user["username"], "logout", school_id=user["school_id"])
        return {"ok": True}

    @app.get("/api/me")
    def me(user: dict = Depends(current_user)):
        school = engine.school(user["school_id"])
        return {**user, "role_name": ROLES[user["role"]], "school_name": school["name"],
                "timezone": school["timezone"],
                "permissions": sorted(p for p, roles in PERMISSIONS.items() if user["role"] in roles)}

    # ---- device -------------------------------------------------------------
    @app.get("/api/device/config")
    def device_config(dev: dict = Depends(current_device)):
        return engine.device_config(dev)

    @app.post("/api/device/heartbeat")
    def heartbeat(dev: dict = Depends(current_device)):
        db.run("UPDATE devices SET last_seen=? WHERE id=?", iso(now()), dev["id"])
        return {"keyword_list_version": engine.school(dev["school_id"])["keyword_version"]}

    @app.post("/api/device/alerts", status_code=201)
    async def device_alert(request: Request, dev: dict = Depends(current_device)):
        raw = await request.body()
        if len(raw) > MAX_ALERT_BYTES:
            raise HTTPException(413, "alerts must be small; audio or transcripts are never accepted")
        try:
            alert = DeviceAlert.model_validate_json(raw)
        except ValidationError as e:
            raise HTTPException(422, e.errors(include_url=False, include_input=False))
        if alert.device_id != dev["id"]:
            raise HTTPException(403, "device id does not match key")
        try:
            inc = engine.ingest_alert(dev, alert)
        except ValueError as e:
            raise HTTPException(400, str(e))
        return {"incident_id": inc["id"], "score": inc["score"], "level": inc["level"], "action": inc["action"]}

    # ---- VMS / AI Cell (cameras outside sensitive areas) -----------------------
    @app.post("/api/vms/events", status_code=201)
    def vms_events(events: list[VideoEvent], x_vms_key: str = Header("")):
        school = db.one("SELECT * FROM schools WHERE json_extract(settings, '$.vms_key')=?", x_vms_key) \
            if x_vms_key else None
        if not school:
            raise HTTPException(401, "unknown VMS key")
        sensitive = {c for loc in db.all("SELECT cameras FROM locations WHERE school_id=? AND sensitive=1", school["id"])
                     for c in loc["cameras"]}
        allowed = {c for loc in db.all("SELECT cameras FROM locations WHERE school_id=?", school["id"])
                   for c in loc["cameras"]}
        rescored: set[int] = set()
        for ev in events:
            if ev.camera_id not in allowed:
                raise HTTPException(400, f"camera {ev.camera_id} is not linked to any location")
            rescored.update(engine.ingest_video(school["id"], ev))
        rescored_out = []
        for i in sorted(rescored):
            inc = engine.incident(i)
            rescored_out.append({k: inc[k] for k in ("id", "score", "level", "action")})
        return {"accepted": len(events), "rescored_incidents": rescored_out,
                "note": "cameras are installed outside washrooms/changing rooms only" if sensitive else ""}

    # ---- incidents ----------------------------------------------------------
    @app.get("/api/incidents")
    def list_incidents(status: str | None = None, level: str | None = None, location_id: str | None = None,
                       include_suppressed: bool = False, limit: int = 100, user: dict = Depends(current_user)):
        sql = ("SELECT i.id, i.ts, i.types, i.score, i.level, i.action, i.status, i.outcome, i.routed_roles, "
               "i.context, i.location_id, l.name AS location_name, l.type AS location_type "
               "FROM incidents i JOIN locations l ON l.id=i.location_id WHERE i.school_id=?")
        args: list = [user["school_id"]]
        if status:
            sql += " AND i.status=?"
            args.append(status)
        elif not include_suppressed:
            sql += " AND i.status<>'suppressed'"
        if level:
            sql += " AND i.level=?"
            args.append(level)
        if location_id:
            sql += " AND i.location_id=?"
            args.append(location_id)
        if not can(user["role"], "incidents:view_all"):
            sql += " AND EXISTS (SELECT 1 FROM json_each(i.routed_roles) WHERE value=?)"
            args.append(user["role"])
        sql += " ORDER BY i.ts DESC LIMIT ?"
        args.append(min(limit, 500))
        return db.all(sql, *args)

    @app.get("/api/incidents/{inc_id}")
    def get_incident(inc_id: int, user: dict = Depends(current_user)):
        inc = visible(engine.incident(inc_id), user)
        auth.audit(user["username"], "view_incident", str(inc_id), school_id=user["school_id"])
        return inc

    @app.post("/api/incidents/{inc_id}/ack")
    def ack(inc_id: int, user: dict = Depends(need("incidents:respond"))):
        inc = visible(engine.incident(inc_id), user)
        if inc["status"] == "resolved":
            raise HTTPException(409, "already resolved")
        db.run("UPDATE incidents SET status='acknowledged', acknowledged_by=?, acknowledged_at=? WHERE id=?",
               user["display_name"], iso(now()), inc_id)
        auth.audit(user["username"], "acknowledge", str(inc_id), school_id=user["school_id"])
        return engine.incident(inc_id)

    @app.post("/api/incidents/{inc_id}/resolve")
    def resolve(inc_id: int, body: ResolveIn, user: dict = Depends(need("incidents:respond"))):
        inc = visible(engine.incident(inc_id), user)
        out = engine.resolve(inc, body.outcome, body.notes, user)
        auth.audit(user["username"], "resolve", str(inc_id), body.outcome, school_id=user["school_id"])
        return out

    @app.get("/api/notifications")
    def notifications(user: dict = Depends(current_user)):
        roles = list(ROLES) if user["role"] == "admin" else [user["role"]]
        q = ",".join("?" * len(roles))
        rows = db.all(f"SELECT * FROM notifications WHERE school_id=? AND role IN ({q}) ORDER BY id DESC LIMIT 50",
                      user["school_id"], *roles)
        for r in rows:
            r["unread"] = user["username"] not in r["read_by"]
        return rows

    @app.post("/api/notifications/read")
    def mark_read(user: dict = Depends(current_user)):
        for n in notifications(user):
            if n["unread"]:
                db.run("UPDATE notifications SET read_by=? WHERE id=?", n["read_by"] + [user["username"]], n["id"])
        return {"ok": True}

    # ---- reports ------------------------------------------------------------
    @app.get("/api/reports/hotspots")
    def hotspots(days: int = 30, user: dict = Depends(need("reports:view"))):
        auth.audit(user["username"], "view_report", f"hotspots:{days}d", school_id=user["school_id"])
        return engine.hotspots(user["school_id"], days)

    # ---- configuration ----------------------------------------------------------
    @app.get("/api/locations")
    def locations(user: dict = Depends(current_user)):
        return db.all("SELECT id, name, type, sensitive, cameras, schedule, cleaning, sensor_confirmed, sensor_false "
                      "FROM locations WHERE school_id=? ORDER BY name", user["school_id"])

    @app.get("/api/devices")
    def devices(user: dict = Depends(need("devices:manage"))):
        return db.all("SELECT d.id, d.name, d.location_id, l.name AS location_name, d.last_seen, "
                      "d.keyword_list_version, d.created_at FROM devices d JOIN locations l ON l.id=d.location_id "
                      "WHERE d.school_id=? ORDER BY d.id", user["school_id"])

    @app.post("/api/devices", status_code=201)
    def add_device(body: DeviceIn, user: dict = Depends(need("devices:manage"))):
        if not db.one("SELECT id FROM locations WHERE id=? AND school_id=?", body.location_id, user["school_id"]):
            raise HTTPException(400, "unknown location")
        key = auth.register_device(user["school_id"], body.id, body.location_id, body.name)
        auth.audit(user["username"], "register_device", body.id, body.location_id, school_id=user["school_id"])
        return {"id": body.id, "device_key": key, "note": "store this key on the device; it is not shown again"}

    @app.get("/api/keywords")
    def keywords(user: dict = Depends(need("keywords:view"))):
        school = engine.school(user["school_id"])
        return {"version": school["keyword_version"], "keywords": engine.keywords(user["school_id"])}

    @app.post("/api/keywords", status_code=201)
    def add_keyword(body: KeywordIn, user: dict = Depends(need("keywords:edit"))):
        if db.one("SELECT id FROM keywords WHERE school_id=? AND term=?", user["school_id"], body.term.strip()):
            raise HTTPException(409, "term already exists")
        kid = db.insert("keywords", school_id=user["school_id"], term=body.term.strip(), lang=body.lang,
                        category=body.category.value, weight=body.weight, created_at=iso(now()))
        engine.bump_keyword_version(user["school_id"])
        auth.audit(user["username"], "add_keyword", str(kid), f"{body.category.value}:{body.term}",
                   school_id=user["school_id"])
        return {"id": kid}

    @app.patch("/api/keywords/{kid}")
    def patch_keyword(kid: int, body: KeywordPatch, user: dict = Depends(need("keywords:edit"))):
        if not db.one("SELECT id FROM keywords WHERE id=? AND school_id=?", kid, user["school_id"]):
            raise HTTPException(404, "keyword not found")
        changes = body.model_dump(exclude_none=True)
        for col, val in changes.items():
            db.run(f"UPDATE keywords SET {col}=? WHERE id=?",
                   int(val) if isinstance(val, bool) else getattr(val, "value", val), kid)
        engine.bump_keyword_version(user["school_id"])
        auth.audit(user["username"], "edit_keyword", str(kid), str(changes), school_id=user["school_id"])
        return {"ok": True}

    @app.delete("/api/keywords/{kid}")
    def delete_keyword(kid: int, user: dict = Depends(need("keywords:edit"))):
        db.run("DELETE FROM keywords WHERE id=? AND school_id=?", kid, user["school_id"])
        engine.bump_keyword_version(user["school_id"])
        auth.audit(user["username"], "delete_keyword", str(kid), school_id=user["school_id"])
        return {"ok": True}

    @app.get("/api/settings")
    def get_settings(user: dict = Depends(need("settings:edit"))):
        s = engine.school(user["school_id"])["settings"]
        cfg = engine.fusion_config(engine.school(user["school_id"]))
        return {"alert_threshold": cfg.alert_threshold, "retention_days": s.get("retention_days", 180),
                "notify_webhooks": s.get("notify_webhooks", {})}

    @app.patch("/api/settings")
    def patch_settings(body: SettingsPatch, user: dict = Depends(need("settings:edit"))):
        school = engine.school(user["school_id"])
        settings = {**school["settings"], **body.model_dump(exclude_none=True)}
        db.run("UPDATE schools SET settings=? WHERE id=?", settings, user["school_id"])
        auth.audit(user["username"], "edit_settings", detail=str(body.model_dump(exclude_none=True)),
                   school_id=user["school_id"])
        return get_settings(user)

    @app.get("/api/users")
    def users(user: dict = Depends(need("users:manage"))):
        return db.all("SELECT id, username, display_name, role, active FROM users WHERE school_id=? ORDER BY role",
                      user["school_id"])

    @app.post("/api/users", status_code=201)
    def add_user(body: UserIn, user: dict = Depends(need("users:manage"))):
        if body.role not in ROLES:
            raise HTTPException(400, "unknown role")
        if db.one("SELECT id FROM users WHERE username=?", body.username):
            raise HTTPException(409, "username taken")
        uid = auth.create_user(user["school_id"], body.username, body.password, body.role, body.display_name)
        auth.audit(user["username"], "add_user", body.username, body.role, school_id=user["school_id"])
        return {"id": uid}

    @app.get("/api/audit")
    def audit(limit: int = 200, user: dict = Depends(need("audit:view"))):
        return db.all("SELECT * FROM audit_log WHERE school_id=? OR school_id IS NULL ORDER BY id DESC LIMIT ?",
                      user["school_id"], min(limit, 1000))

    return app

