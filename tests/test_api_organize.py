from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mortis.api.app import create_app
from mortis.settings import Settings

LOCAL = "http://127.0.0.1"


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    app = create_app(Settings(data_dir=tmp_path / "data"), start_worker=False)
    with TestClient(app, base_url=LOCAL) as opened:
        yield opened


def test_organize_enqueues_once(client: TestClient, tmp_path: Path) -> None:
    root = tmp_path / "电影"
    root.mkdir()
    library = client.post(
        "/api/libraries", json={"name": "电影", "data_type_id": "movie", "paths": [str(root)]}
    ).json()
    first = client.post(f"/api/libraries/{library['id']}/organize")
    assert first.status_code == 202
    again = client.post(f"/api/libraries/{library['id']}/organize", json={"scrape": False})
    assert again.json() == first.json()
    task = client.get(f"/api/tasks/{first.json()['task_id']}").json()
    assert (task["task_type"], task["type_label"], task["title"]) == (
        "organize",
        "扫描媒体库",
        "电影",
    )
    assert task["payload"] == {"library_id": library["id"], "scrape": None}


def test_organize_unknown_library(client: TestClient) -> None:
    assert client.post("/api/libraries/999/organize").status_code == 404
