from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mortis.api.app import create_app
from mortis.settings import Settings
from mortis.version import get_version


@pytest.fixture
def web_dist(tmp_path: Path) -> Path:
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>莫提斯</html>", encoding="utf-8")
    (dist / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("不应返回", encoding="utf-8")
    return dist


def make_client(tmp_path: Path, web_dist: Path | None = None) -> TestClient:
    settings = Settings(data_dir=tmp_path / "data", web_dist=web_dist)
    return TestClient(create_app(settings))


def test_health(tmp_path: Path) -> None:
    response = make_client(tmp_path).get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_system_info(tmp_path: Path) -> None:
    response = make_client(tmp_path).get("/api/system/info")
    assert response.status_code == 200
    assert response.json() == {
        "name": "莫提斯",
        "version": get_version(),
        "data_dir": str(tmp_path / "data"),
    }


def test_without_web_dist_root_is_404(tmp_path: Path) -> None:
    assert make_client(tmp_path).get("/").status_code == 404


@pytest.mark.parametrize(
    ("path", "status", "body"),
    [
        ("/", 200, "<html>莫提斯</html>"),
        ("/library/1", 200, "<html>莫提斯</html>"),
        ("/assets/app.js", 200, "console.log(1)"),
        ("/../secret.txt", 200, "<html>莫提斯</html>"),
        ("/assets/../../secret.txt", 200, "<html>莫提斯</html>"),
    ],
)
def test_web_dist_serving(
    tmp_path: Path, web_dist: Path, path: str, status: int, body: str
) -> None:
    response = make_client(tmp_path, web_dist).get(path)
    assert response.status_code == status
    assert response.text == body


@pytest.mark.parametrize("path", ["/api/unknown", "/api/system/nothing"])
def test_unknown_api_is_404_even_with_web_dist(tmp_path: Path, web_dist: Path, path: str) -> None:
    assert make_client(tmp_path, web_dist).get(path).status_code == 404


def test_api_still_works_with_web_dist(tmp_path: Path, web_dist: Path) -> None:
    response = make_client(tmp_path, web_dist).get("/api/health")
    assert response.json() == {"status": "ok"}


def test_web_dist_without_index_is_rejected(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(FileNotFoundError, match="index.html"):
        create_app(Settings(data_dir=tmp_path / "data", web_dist=empty))
