import json

from safesound.static_export import build


def test_static_export(tmp_path):
    build(tmp_path)
    html = (tmp_path / "index.html").read_text()
    assert "window.SAFESOUND_STATIC = true" in html
    bus = json.loads((tmp_path / "demo-data" / "bus.json").read_text())
    assert bus["incidents"] and all("bus_attendant" in i["routed_roles"] for i in bus["incidents"])
    assert bus["hotspots"]["30"] is None and bus["keywords"] is None  # role limits carried into the snapshot
    admin = json.loads((tmp_path / "demo-data" / "admin.json").read_text())
    assert set(admin["details"]) == {str(i["id"]) for i in admin["incidents"]}
    assert admin["hotspots"]["30"]["total"] > 0
