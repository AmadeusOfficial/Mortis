from pathlib import Path
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from mortis.api.app import create_app
from mortis.settings import Settings
from mortis.version import get_version

LOCAL = "http://127.0.0.1"  # TestClient 默认的 testserver 不在主机白名单里


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
    return TestClient(create_app(settings), base_url=LOCAL)


@pytest.mark.parametrize("path", ["/api/health", "/", "/library/1"])
def test_foreign_host_is_rejected(tmp_path: Path, web_dist: Path, path: str) -> None:
    client = make_client(tmp_path, web_dist)
    response = client.get(path, headers={"Host": "evil.example"})
    assert response.status_code == 400


@pytest.mark.parametrize("host", ["127.0.0.1:8100", "localhost:5174", "localhost", "LOCALHOST:1"])
@pytest.mark.parametrize("path", ["/api/health", "/"])
def test_local_hosts_are_allowed(tmp_path: Path, web_dist: Path, host: str, path: str) -> None:
    client = make_client(tmp_path, web_dist)
    assert client.get(path, headers={"Host": host}).status_code == 200


def test_testserver_host_is_not_allowed(tmp_path: Path) -> None:
    client = TestClient(create_app(Settings(data_dir=tmp_path / "data")))
    assert client.get("/api/health").status_code == 400


@pytest.mark.parametrize("host", ["0.0.0.0", "::"])
def test_wildcard_bind_accepts_any_host(tmp_path: Path, host: str) -> None:
    """Docker 里绑 0.0.0.0，从别的机器用 NAS/主机的局域网 IP 打开页面要能用。"""
    app = create_app(Settings(data_dir=tmp_path / "data", host=host))
    client = TestClient(app, base_url=LOCAL)
    for name in ("192.168.3.200:8100", "127.0.0.1:8100", "nas.local:8100", "10.0.0.7"):
        assert client.get("/api/health", headers={"Host": name}).status_code == 200


@pytest.mark.parametrize(
    ("host", "allowed", "denied"),
    [
        ("192.168.1.5", ["192.168.1.5:8100", "localhost"], ["10.0.0.1"]),
        # 绑通配地址就是给局域网用的，任何 Host 都放行，换 IP 也不会断。
        ("0.0.0.0", ["127.0.0.1", "192.168.3.200:8100", "10.0.0.1"], []),
        ("::", ["localhost", "192.168.3.200:8100"], []),
        ("localhost", ["localhost", "127.0.0.1"], ["10.0.0.1"]),
    ],
)
def test_configured_host_joins_allow_list(
    tmp_path: Path, host: str, allowed: list[str], denied: list[str]
) -> None:
    app = create_app(Settings(data_dir=tmp_path / "data", host=host))
    client = TestClient(app, base_url=LOCAL)
    for name in allowed:
        assert client.get("/api/health", headers={"Host": name}).status_code == 200
    for name in denied:
        assert client.get("/api/health", headers={"Host": name}).status_code == 400


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
        ("/%2e%2e/secret.txt", 200, "<html>莫提斯</html>"),
        ("/..%5Csecret.txt", 200, "<html>莫提斯</html>"),
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


def spy_disk_access(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    touched: list[str] = []
    for name in ("resolve", "is_file"):
        original = getattr(Path, name)

        def spy(self: Path, *args: object, _original=original, **kwargs: object) -> object:
            touched.append(str(self))
            return _original(self, *args, **kwargs)

        monkeypatch.setattr(Path, name, spy)
    return touched


def test_unc_path_never_touches_disk(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, web_dist: Path
) -> None:
    client = make_client(tmp_path, web_dist)
    touched = spy_disk_access(monkeypatch)
    response = client.get("/%5C%5Cexample.invalid%5Cs%5Cx")
    assert response.status_code == 200
    assert response.text == "<html>莫提斯</html>"
    assert not [p for p in touched if "example.invalid" in p]


@pytest.mark.parametrize("encode_slash", [True, False])
def test_drive_absolute_path_is_not_served(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, web_dist: Path, encode_slash: bool
) -> None:
    target = (tmp_path / "secret.txt").as_posix()
    request_path = "/" + quote(target, safe="" if encode_slash else "/")
    client = make_client(tmp_path, web_dist)
    touched = spy_disk_access(monkeypatch)
    response = client.get(request_path)
    assert response.status_code == 200
    assert response.text == "<html>莫提斯</html>"
    assert not [p for p in touched if "secret.txt" in p]


def test_drive_relative_path_is_not_served(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, web_dist: Path
) -> None:
    client = make_client(tmp_path, web_dist)
    touched = spy_disk_access(monkeypatch)
    response = client.get("/C:secret.txt")
    assert response.text == "<html>莫提斯</html>"
    assert not [p for p in touched if "secret.txt" in p]


def test_disk_error_falls_back_to_index(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, web_dist: Path
) -> None:
    client = make_client(tmp_path, web_dist)
    original = Path.resolve

    def failing(self: Path, *args: object, **kwargs: object) -> Path:
        if "boom" in str(self):
            raise OSError("模拟磁盘错误")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", failing)
    response = client.get("/boom.js")
    assert response.status_code == 200
    assert response.text == "<html>莫提斯</html>"


@pytest.mark.parametrize(("path", "status"), [("/", 200), ("/assets/app.js", 200), ("/api/x", 404)])
def test_head_requests(tmp_path: Path, web_dist: Path, path: str, status: int) -> None:
    response = make_client(tmp_path, web_dist).head(path)
    assert response.status_code == status
    assert response.content == b""


def test_web_dist_without_index_is_rejected(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(FileNotFoundError, match="index.html"):
        create_app(Settings(data_dir=tmp_path / "data", web_dist=empty))
