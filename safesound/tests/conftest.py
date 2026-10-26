import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from safesound.server.app import create_app  # noqa: E402
from safesound.simulator import Simulator  # noqa: E402


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(str(tmp_path / "test.db"), demo=True))


@pytest.fixture
def sim(client):
    def post(path, body, headers):
        r = client.post(path, json=body, headers=headers)
        assert r.status_code < 300, r.text
        return r.json()

    def get(path, headers):
        r = client.get(path, headers=headers)
        assert r.status_code == 200, r.text
        return r.json()

    return Simulator(post, get)


def login(client, username, password="safesound123"):
    r = client.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}
