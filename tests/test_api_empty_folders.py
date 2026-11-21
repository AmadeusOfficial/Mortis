from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mortis.api.app import create_app
from mortis.settings import Settings

LOCAL = "http://127.0.0.1"


@pytest.fixture
def app(tmp_path: Path) -> FastAPI:
    return create_app(Settings(data_dir=tmp_path / "data"), start_worker=False)


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app, base_url=LOCAL) as opened:
        yield opened


@pytest.fixture
def library(client: TestClient, tmp_path: Path) -> int:
    root = tmp_path / "动漫"
    root.mkdir()
    created = client.post(
        "/api/libraries", json={"name": "动漫", "data_type_id": "anime", "paths": [str(root)]}
    ).json()
    return created["id"]


def test_lists_only_hollow_folders(client: TestClient, library: int, tmp_path: Path) -> None:
    root = tmp_path / "动漫"
    # 有视频的剧：留着
    kept = root / "有片 (2021)" / "Season 1"
    kept.mkdir(parents=True)
    (kept / "a.mkv").write_bytes(b"x")
    # 空的剧目录，带两个空季：要删
    (root / "空的 (2012)" / "Season 1").mkdir(parents=True)
    (root / "空的 (2012)" / "Season 2").mkdir(parents=True)
    # 隐藏目录和下载临时目录：跳过
    (root / ".deletedByTMM" / "旧").mkdir(parents=True)
    (root / "@DownloadStationTempFiles" / "tmp").mkdir(parents=True)

    body = client.get(f"/api/libraries/{library}/empty-folders").json()
    assert [Path(f["path"]).name for f in body["folders"]] == ["空的 (2012)"]
    assert body["folders"][0]["nested"] == 3
    assert body["total"] == 3

    # 只读：目录还在
    assert (root / "空的 (2012)" / "Season 1").is_dir()


def test_deletes_hollow_folders_only(client: TestClient, library: int, tmp_path: Path) -> None:
    root = tmp_path / "动漫"
    kept = root / "有片 (2021)" / "Season 1"
    kept.mkdir(parents=True)
    (kept / "a.mkv").write_bytes(b"x")
    (root / "空的 (2012)" / "Season 1").mkdir(parents=True)
    (root / ".deletedByTMM").mkdir()

    result = client.post(f"/api/libraries/{library}/empty-folders", json={"paths": []}).json()
    assert result["removed"] == 2 and result["failed"] == []
    assert not (root / "空的 (2012)").exists()
    assert (kept / "a.mkv").read_bytes() == b"x"
    assert (root / ".deletedByTMM").is_dir()
    assert client.get(f"/api/libraries/{library}/empty-folders").json()["total"] == 0


def test_deletes_only_given_folder(client: TestClient, library: int, tmp_path: Path) -> None:
    root = tmp_path / "动漫"
    (root / "甲 (2001)").mkdir(parents=True)
    (root / "乙 (2002)").mkdir(parents=True)
    chosen = str(root / "甲 (2001)")
    result = client.post(f"/api/libraries/{library}/empty-folders", json={"paths": [chosen]}).json()
    assert result["removed"] == 1
    assert not (root / "甲 (2001)").exists()
    assert (root / "乙 (2002)").is_dir()


def test_skips_folder_that_fills_up(client: TestClient, library: int, tmp_path: Path) -> None:
    """预览之后有文件写进来，就不再删它。"""
    root = tmp_path / "动漫"
    folder = root / "刚建 (2026)" / "Season 1"
    folder.mkdir(parents=True)
    preview = client.get(f"/api/libraries/{library}/empty-folders").json()
    assert preview["total"] == 2
    (folder / "b.mkv").write_bytes(b"y")

    result = client.post(f"/api/libraries/{library}/empty-folders", json={"paths": []}).json()
    assert result["removed"] == 0
    assert (folder / "b.mkv").read_bytes() == b"y"


def test_unknown_library(client: TestClient) -> None:
    assert client.get("/api/libraries/999/empty-folders").status_code == 404
