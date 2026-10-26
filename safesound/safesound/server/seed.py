"""Demo school, staff accounts and devices."""
from __future__ import annotations

from ..scenarios import DEMO_LOCATIONS
from .auth import Auth
from .engine import Engine

DEMO_SCHOOL = "demo"
DEMO_VMS_KEY = "demo-vms-key"
DEMO_PASSWORD = "safesound123"
DEMO_USERS = [
    ("admin", "admin", "School Admin"),
    ("principal", "management", "Principal"),
    ("supervisor", "supervisor", "Floor Supervisor"),
    ("security", "security", "Security Desk"),
    ("pe", "pe_teacher", "PE Teacher"),
    ("bus", "bus_attendant", "Bus Attendant"),
    ("counselor", "counselor", "School Counselor"),
]


def demo_device_key(location_id: str) -> str:
    return f"demo-key-{location_id}"


def seed_demo(engine: Engine, auth: Auth) -> None:
    engine.create_school(DEMO_SCHOOL, "SafeSound Demo School", settings={
        "vms_key": DEMO_VMS_KEY, "alert_threshold": 60, "retention_days": 180})
    for loc in DEMO_LOCATIONS:
        engine.add_location(DEMO_SCHOOL, loc)
        auth.register_device(DEMO_SCHOOL, f"ss-{loc['id']}", loc["id"], f"SafeSound unit – {loc['name']}",
                             key=demo_device_key(loc["id"]))
    for username, role, name in DEMO_USERS:
        auth.create_user(DEMO_SCHOOL, username, DEMO_PASSWORD, role, name)
    auth.audit("system", "seed_demo", DEMO_SCHOOL, school_id=DEMO_SCHOOL)
