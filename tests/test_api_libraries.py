import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from mortis.api.app import create_app
from mortis.db.models import Item, ItemSourceRecord, MediaFile, Task, utc_now
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
    assert body["is_auto_scrape"] is True
    assert (body["item_count"], body["file_count"]) == (0, 0)
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
        "/api/libraries",
        json={"name": "x", "data_type_id": "nosuchtype", "paths": [str(dirs["a"])]},
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


@pytest.mark.parametrize("field", ["data_type_id", "automation", "id"])
def test_update_rejects_unknown_fields(
    client: TestClient, dirs: dict[str, Path], field: str
) -> None:
    created = _create(client, [str(dirs["a"])]).json()
    response = client.patch(f"/api/libraries/{created['id']}", json={field: "tv"})
    assert response.status_code == 422
    assert client.get(f"/api/libraries/{created['id']}").json() == created


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


def test_delete_removes_items_and_files_but_not_disk(
    client: TestClient, dirs: dict[str, Path]
) -> None:
    library = _create(client, [str(dirs["a"])]).json()
    video = dirs["a"] / "片.mkv"
    video.write_bytes(b"x")
    factory = client.app.state.session_factory
    with factory() as session:
        now = utc_now()
        item = Item(
            library_id=library["id"],
            data_type_id="movie",
            kind="work",
            title="片",
            created_at=now,
            updated_at=now,
        )
        session.add(item)
        session.flush()
        session.add(
            MediaFile(
                library_id=library["id"],
                item_id=item.id,
                path=str(video),
                file_kind="video",
                size=1,
                created_at=now,
                updated_at=now,
            )
        )
        session.commit()
    listed = client.get("/api/libraries").json()[0]
    assert (listed["item_count"], listed["file_count"]) == (1, 1)
    assert client.delete(f"/api/libraries/{library['id']}").status_code == 204
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(Item)) == 0
        assert session.scalar(select(func.count()).select_from(MediaFile)) == 0
    assert video.is_file()


def test_toggle_auto_scrape(client: TestClient, dirs: dict[str, Path]) -> None:
    library = _create(client, [str(dirs["a"])]).json()
    body = client.patch(f"/api/libraries/{library['id']}", json={"is_auto_scrape": False}).json()
    assert body["is_auto_scrape"] is False
    assert body["paths"] == library["paths"]


def test_stats_scraped_issues_and_sample_posters(client: TestClient, dirs: dict[str, Path]) -> None:
    library = _create(client, [str(dirs["a"])]).json()
    factory = client.app.state.session_factory
    with factory() as session:
        now = utc_now()
        scraped = Item(
            library_id=library["id"],
            data_type_id="movie",
            kind="work",
            title="已刮削",
            poster_path="aa/早期.jpg",
            created_at="2026-01-01T00:00:00+00:00",
            updated_at=now,
        )
        failed = Item(
            library_id=library["id"],
            data_type_id="movie",
            kind="work",
            title="失败",
            poster_path="bb/较新.jpg",
            created_at="2026-01-02T00:00:00+00:00",
            updated_at=now,
        )
        recovered = Item(
            library_id=library["id"],
            data_type_id="movie",
            kind="work",
            title="失败后成功",
            created_at="2026-01-03T00:00:00+00:00",
            updated_at=now,
        )
        session.add_all([scraped, failed, recovered])
        session.flush()
        session.add(ItemSourceRecord(item_id=scraped.id, source="tmdb", fetched_at=now, raw="{}"))
        session.add_all(
            [
                Task(
                    task_type="scrape",
                    status="failed",
                    payload=json.dumps({"item_id": failed.id, "force": False}),
                    error="连接 TMDB 失败",
                    created_at=now,
                ),
                Task(
                    task_type="scrape",
                    status="failed",
                    payload=json.dumps({"item_id": recovered.id, "force": False}),
                    error="连接 TMDB 失败",
                    created_at=now,
                ),
                Task(
                    task_type="scrape",
                    status="succeeded",
                    payload=json.dumps({"item_id": recovered.id, "force": False}),
                    created_at=now,
                ),
            ]
        )
        session.commit()
        ids = {"scraped": scraped.id, "failed": failed.id, "recovered": recovered.id}
    assert ids["recovered"] > ids["failed"] > ids["scraped"]
    body = client.get(f"/api/libraries/{library['id']}").json()
    assert body["item_count"] == 3
    assert body["scraped_count"] == 1
    assert body["issue_count"] == 1
    assert body["sample_posters"] == ["/api/images/bb/较新.jpg", "/api/images/aa/早期.jpg"]
    assert client.get("/api/libraries").json() == [body]


def test_sample_posters_keeps_latest_eight(client: TestClient, dirs: dict[str, Path]) -> None:
    library = _create(client, [str(dirs["a"])]).json()
    factory = client.app.state.session_factory
    with factory() as session:
        now = utc_now()
        session.add_all(
            [
                Item(
                    library_id=library["id"],
                    data_type_id="movie",
                    kind="work",
                    title=f"第 {index} 部",
                    poster_path=None if index == 9 else f"c{index}/海报.jpg",
                    created_at=f"2026-02-{index + 1:02d}T00:00:00+00:00",
                    updated_at=now,
                )
                for index in range(10)
            ]
        )
        session.commit()
    body = client.get(f"/api/libraries/{library['id']}").json()
    assert body["sample_posters"] == [f"/api/images/c{index}/海报.jpg" for index in range(8, 0, -1)]


def test_libraries_survive_restart(data_dir: Path, dirs: dict[str, Path]) -> None:
    with TestClient(create_app(Settings(data_dir=data_dir)), base_url=LOCAL) as first:
        created = _create(first, [str(dirs["a"])]).json()
    with TestClient(create_app(Settings(data_dir=data_dir)), base_url=LOCAL) as second:
        assert second.get("/api/libraries").json() == [created]
