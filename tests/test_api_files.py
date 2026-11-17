"""目录浏览接口：默认只列子目录，带 files 时也列文件。"""

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


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    root = tmp_path / "浏览"
    (root / "子目录").mkdir(parents=True)
    (root / ".隐藏").mkdir()
    (root / "b.exe").write_bytes(b"MZ")
    (root / "A.txt").write_text("x", encoding="utf-8")
    (root / ".hidden.exe").write_bytes(b"MZ")
    return root


def test_dirs_only_by_default(client: TestClient, folder: Path) -> None:
    response = client.get("/api/files/dirs", params={"path": str(folder)})
    assert response.status_code == 200, response.text
    body = response.json()
    assert [entry["name"] for entry in body["dirs"]] == ["子目录"]
    assert body["files"] == []


def test_files_listed_when_asked(client: TestClient, folder: Path) -> None:
    response = client.get("/api/files/dirs", params={"path": str(folder), "files": "true"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert [entry["name"] for entry in body["dirs"]] == ["子目录"]
    assert body["files"] == [
        {"name": "A.txt", "path": str(folder / "A.txt")},
        {"name": "b.exe", "path": str(folder / "b.exe")},
    ]


def test_drive_list_has_no_files(client: TestClient) -> None:
    body = client.get("/api/files/dirs", params={"files": "true"}).json()
    assert body["path"] == "" and body["files"] == []
