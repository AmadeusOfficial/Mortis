"""在资源管理器里显示条目的文件：选中文件；文件没了退到所在目录；目录也没了报 404。"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mortis.api.app import create_app
from mortis.db.models import MediaFile, utc_now
from mortis.settings import Settings
from tests.fakes import FakeLauncher

LOCAL = "http://127.0.0.1"


@pytest.fixture
def launcher() -> FakeLauncher:
    return FakeLauncher()


@pytest.fixture
def app(tmp_path: Path, launcher: FakeLauncher) -> FastAPI:
    return create_app(Settings(data_dir=tmp_path / "data"), start_worker=False, launcher=launcher)


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app, base_url=LOCAL) as opened:
        yield opened


@pytest.fixture
def library(client: TestClient, tmp_path: Path) -> int:
    root = tmp_path / "电影"
    root.mkdir()
    body = {"name": "电影", "data_type_id": "movie", "paths": [str(root)]}
    return client.post("/api/libraries", json=body).json()["id"]


def _file(app: FastAPI, library_id: int, path: Path) -> int:
    with app.state.session_factory() as session:
        now = utc_now()
        row = MediaFile(
            library_id=library_id,
            path=str(path),
            file_kind="subtitle",
            size=1,
            created_at=now,
            updated_at=now,
        )
        session.add(row)
        session.commit()
        return row.id


def test_reveal_selects_file(
    app: FastAPI, client: TestClient, library: int, tmp_path: Path, launcher: FakeLauncher
) -> None:
    path = tmp_path / "电影" / "低俗小说 (1994)" / "低俗小说, 2160p.ass"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"x")
    file_id = _file(app, library, path)

    response = client.post(f"/api/media-files/{file_id}/reveal")
    assert response.status_code == 204, response.text
    assert launcher.revealed == [(str(path), True)]


def test_reveal_falls_back_to_folder_or_404(
    app: FastAPI, client: TestClient, library: int, tmp_path: Path, launcher: FakeLauncher
) -> None:
    folder = tmp_path / "电影" / "盗火线 (1995)"
    folder.mkdir()
    gone = _file(app, library, folder / "没了.mkv")
    response = client.post(f"/api/media-files/{gone}/reveal")
    assert response.status_code == 204, response.text
    assert launcher.revealed == [(str(folder), False)]

    lost_path = tmp_path / "电影" / "不在了" / "x.mkv"
    lost = _file(app, library, lost_path)
    response = client.post(f"/api/media-files/{lost}/reveal")
    assert response.status_code == 404
    assert response.json()["detail"] == f"文件和所在目录都不在了：{lost_path}"

    assert client.post("/api/media-files/99999/reveal").status_code == 404

    launcher.fail = PermissionError(13, "拒绝访问。")
    response = client.post(f"/api/media-files/{gone}/reveal")
    assert response.status_code == 502
    assert response.json()["detail"] == "打不开资源管理器：拒绝访问。"
