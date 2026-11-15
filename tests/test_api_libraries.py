from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mortis.api.app import create_app
from mortis.db.models import Item, utc_now
from mortis.settings import Settings

LOCAL = "http://127.0.0.1"  # TestClient 默认的 testserver 不在主机白名单里


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    return tmp_path / "data"


@pytest.fixture
def client(data_dir: Path) -> Iterator[TestClient]:
    with TestClient(create_app(Settings(data_dir=data_dir)), base_url=LOCAL) as opened:
        yield opened


@pytest.fixture
def dirs(tmp_path: Path) -> dict[str, Path]:
    result = {
        "a": tmp_path / "影片" / "A",
        "b": tmp_path / "影片" / "B",
        "a_child": tmp_path / "影片" / "A" / "子目录",
    }
    for path in result.values():
        path.mkdir(parents=True, exist_ok=True)
    return result


def _create(client: TestClient, paths: list[str], name: str = "电影"):  # noqa: ANN202
    return client.post(
        "/api/libraries", json={"name": name, "data_type_id": "movie", "paths": paths}
    )


def test_create_and_list(client: TestClient, dirs: dict[str, Path]) -> None:
    response = _create(client, [str(dirs["a"])])
    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "电影"
    assert body["data_type_id"] == "movie"
    assert body["data_type_name"] == "电影"
    assert body["automation"] == "manual"
    assert body["paths"] == [str(dirs["a"])]
    assert body["created_at"] == body["updated_at"]
    assert client.get("/api/libraries").json() == [body]
    assert client.get(f"/api/libraries/{body['id']}").json() == body


def test_quotes_and_trailing_separator_are_cleaned(
    client: TestClient, dirs: dict[str, Path]
) -> None:
    raw = f'  "{dirs["a"]}\\"  '
    response = _create(client, [raw, "   "])
    assert response.status_code == 201
    assert response.json()["paths"] == [str(dirs["a"])]


@pytest.mark.parametrize(
    ("paths", "message"),
    [
        (["relative\\dir"], "目录必须是绝对路径"),
        ([], "至少要选一个目录"),
        (["   "], "至少要选一个目录"),
    ],
)
def test_bad_paths_rejected(client: TestClient, paths: list[str], message: str) -> None:
    response = _create(client, paths)
    assert response.status_code == 422
    assert message in response.json()["detail"]


def test_missing_dir_rejected(client: TestClient, tmp_path: Path) -> None:
    response = _create(client, [str(tmp_path / "没有这个目录")])
    assert response.status_code == 422
    assert "目录不存在" in response.json()["detail"]


def test_duplicate_in_same_request_rejected(client: TestClient, dirs: dict[str, Path]) -> None:
    response = _create(client, [str(dirs["a"]), str(dirs["a"]).upper()])
    assert response.status_code == 422
    assert "目录重复或互相包含" in response.json()["detail"]


def test_nested_in_same_request_rejected(client: TestClient, dirs: dict[str, Path]) -> None:
    response = _create(client, [str(dirs["a"]), str(dirs["a_child"])])
    assert response.status_code == 422


@pytest.mark.parametrize("key", ["a", "a_child"])
def test_overlap_with_other_library_rejected(
    client: TestClient, dirs: dict[str, Path], key: str
) -> None:
    _create(client, [str(dirs["a"])], name="已有的库")
    response = _create(client, [str(dirs[key])], name="新库")
    assert response.status_code == 409
    assert "已有的库" in response.json()["detail"]


@pytest.mark.parametrize(
    ("name", "message"), [("   ", "库名不能为空"), ("长" * 51, "库名不能超过 50 个字")]
)
def test_bad_name_rejected(
    client: TestClient, dirs: dict[str, Path], name: str, message: str
) -> None:
    response = _create(client, [str(dirs["a"])], name=name)
    assert response.status_code == 422
    assert message in response.json()["detail"]


def test_unknown_data_type_rejected(client: TestClient, dirs: dict[str, Path]) -> None:
    response = client.post(
        "/api/libraries", json={"name": "x", "data_type_id": "galgame", "paths": [str(dirs["a"])]}
    )
    assert response.status_code == 422
    assert "没有这种数据类型" in response.json()["detail"]


def test_update_name_and_paths(client: TestClient, dirs: dict[str, Path]) -> None:
    created = _create(client, [str(dirs["a"])]).json()
    response = client.patch(
        f"/api/libraries/{created['id']}",
        json={"name": " 电影库 ", "paths": [str(dirs["a"]), str(dirs["b"])]},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "电影库"
    assert body["paths"] == [str(dirs["a"]), str(dirs["b"])]
    assert body["data_type_id"] == "movie"


def test_update_can_narrow_to_own_subdir(client: TestClient, dirs: dict[str, Path]) -> None:
    created = _create(client, [str(dirs["a"])]).json()
    response = client.patch(
        f"/api/libraries/{created['id']}", json={"paths": [str(dirs["a_child"])]}
    )
    assert response.status_code == 200
    assert response.json()["paths"] == [str(dirs["a_child"])]


def test_missing_library_is_404(client: TestClient) -> None:
    assert client.get("/api/libraries/999").status_code == 404
    assert client.patch("/api/libraries/999", json={"name": "x"}).status_code == 404
    response = client.delete("/api/libraries/999")
    assert response.status_code == 404
    assert response.json()["detail"] == "没有这个媒体库"


def test_delete_frees_paths(client: TestClient, dirs: dict[str, Path]) -> None:
    created = _create(client, [str(dirs["a"])]).json()
    assert client.delete(f"/api/libraries/{created['id']}").status_code == 204
    assert client.get("/api/libraries").json() == []
    assert dirs["a"].is_dir()
    assert _create(client, [str(dirs["a"])]).status_code == 201


def test_delete_refused_when_library_has_items(client: TestClient, dirs: dict[str, Path]) -> None:
    created = _create(client, [str(dirs["a"])]).json()
    with client.app.state.session_factory() as session:
        now = utc_now()
        session.add(
            Item(
                library_id=created["id"],
                data_type_id="movie",
                kind="work",
                title="教父",
                created_at=now,
                updated_at=now,
            )
        )
        session.commit()
    response = client.delete(f"/api/libraries/{created['id']}")
    assert response.status_code == 409
    assert response.json()["detail"] == "库里还有条目或文件，不能删除"


def test_libraries_survive_restart(data_dir: Path, dirs: dict[str, Path]) -> None:
    with TestClient(create_app(Settings(data_dir=data_dir)), base_url=LOCAL) as first:
        created = _create(first, [str(dirs["a"])]).json()
    with TestClient(create_app(Settings(data_dir=data_dir)), base_url=LOCAL) as second:
        assert second.get("/api/libraries").json() == [created]
