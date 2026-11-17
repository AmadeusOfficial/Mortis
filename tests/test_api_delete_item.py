"""删除条目：独占文件夹、共用目录、越界拒绝、任务、预览一致、部分失败、系列和图片缓存。"""

import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from mortis.api.app import create_app
from mortis.db.models import Item, ItemImage, Library, LibraryPath, MediaFile, Task, utc_now
from mortis.settings import Settings
from tests.fakes import FakeTrash, make_junction

LOCAL = "http://127.0.0.1"
IMAGE_A = "aa/" + "a" * 64 + ".jpg"
IMAGE_B = "bb/" + "b" * 64 + ".jpg"
IMAGE_SHARED = "cc/" + "c" * 64 + ".jpg"


@pytest.fixture
def trash() -> FakeTrash:
    return FakeTrash()


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    return tmp_path / "data"


@pytest.fixture
def app(data_dir: Path, trash: FakeTrash) -> FastAPI:
    return create_app(Settings(data_dir=data_dir), start_worker=False, trash=trash)


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app, base_url=LOCAL) as opened:
        yield opened


@pytest.fixture
def root(tmp_path: Path) -> Path:
    folder = tmp_path / "lib"
    folder.mkdir()
    return folder


def _library(client: TestClient, root: Path, data_type: str = "movie") -> int:
    root.mkdir(parents=True, exist_ok=True)
    body = {"name": root.name, "data_type_id": data_type, "paths": [str(root)]}
    response = client.post("/api/libraries", json=body)
    assert response.status_code in (200, 201), response.text
    return response.json()["id"]


def _item(
    app: FastAPI,
    library_id: int,
    title: str = "教父",
    *,
    data_type: str = "movie",
    kind: str = "work",
    parent_id: int | None = None,
    poster: str | None = None,
) -> int:
    with app.state.session_factory() as session:
        now = utc_now()
        row = Item(
            library_id=library_id,
            data_type_id=data_type,
            kind=kind,
            parent_id=parent_id,
            title=title,
            poster_path=poster,
            created_at=now,
            updated_at=now,
        )
        session.add(row)
        session.commit()
        return row.id


def _file(
    app: FastAPI,
    library_id: int,
    item_id: int | None,
    path: Path,
    kind: str = "video",
    *,
    content: bytes = b"x",
) -> int:
    if kind == "game":
        path.mkdir(parents=True, exist_ok=True)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    with app.state.session_factory() as session:
        now = utc_now()
        row = MediaFile(
            library_id=library_id,
            item_id=item_id,
            path=str(path),
            file_kind=kind,
            size=None if kind == "game" else len(content),
            created_at=now,
            updated_at=now,
        )
        session.add(row)
        session.commit()
        return row.id


def _preview(client: TestClient, item_id: int) -> dict[str, Any]:
    response = client.get(f"/api/items/{item_id}/delete-preview")
    assert response.status_code == 200, response.text
    return response.json()


def _expected(preview: dict[str, Any]) -> dict[str, Any]:
    return {
        "folders": preview["folders"],
        "files": preview["files"],
        "file_count": preview["file_count"],
        "total_size": preview["total_size"],
        "permanent": preview["permanent"],
        "unregistered_count": preview["unregistered"]["count"],
    }


EMPTY_EXPECTED = {
    "folders": [],
    "files": [],
    "file_count": 0,
    "total_size": 0,
    "permanent": False,
    "unregistered_count": 0,
}


def _delete(client: TestClient, item_id: int, preview: dict[str, Any], title: str | None = None):
    body = {
        "confirm_title": preview["title"] if title is None else title,
        "expected": _expected(preview),
    }
    return client.request("DELETE", f"/api/items/{item_id}", json=body)


def _gone(app: FastAPI, item_id: int) -> bool:
    with app.state.session_factory() as session:
        return session.get(Item, item_id) is None


def _file_paths(app: FastAPI) -> set[str]:
    with app.state.session_factory() as session:
        return set(session.scalars(select(MediaFile.path)))


def test_exclusive_folder_deleted_whole(client: TestClient, app: FastAPI, root: Path) -> None:
    library = _library(client, root)
    item = _item(app, library)
    folder = root / "教父 (1972)"
    _file(app, library, item, folder / "教父.mkv", content=b"12345")
    _file(app, library, item, folder / "教父.nfo", "nfo", content=b"12")
    (folder / "未登记.txt").write_bytes(b"abc")
    (folder / "extras").mkdir()
    (folder / "extras" / "说明.txt").write_bytes(b"abcd")
    (root / "别的.txt").write_bytes(b"keep")

    preview = _preview(client, item)
    assert preview == {
        "item_id": item,
        "title": "教父",
        "library_id": library,
        "folders": [str(folder)],
        "files": [],
        "file_count": 4,
        "total_size": 14,
        "permanent": False,
        "unregistered": {
            "count": 2,
            "total_size": 7,
            "sample": sorted([str(Path("extras") / "说明.txt"), "未登记.txt"]),
        },
        "reasons": preview["reasons"],
    }

    response = _delete(client, item, preview)
    assert response.status_code == 200, response.text
    assert response.json() == {"deleted": True, "library_id": library}
    assert not folder.exists()
    assert root.is_dir()
    assert (root / "别的.txt").exists()
    assert _gone(app, item)
    assert _file_paths(app) == set()


def test_shared_folder_only_own_files_and_empty_dirs(
    client: TestClient, app: FastAPI, root: Path, trash: FakeTrash
) -> None:
    library = _library(client, root)
    mine = _item(app, library, "甲")
    other = _item(app, library, "乙")
    shared = root / "合集"
    _file(app, library, mine, shared / "甲.mkv")
    _file(app, library, mine, shared / "字幕" / "甲.srt", "subtitle")
    _file(app, library, other, shared / "乙.mkv")

    preview = _preview(client, mine)
    assert preview["folders"] == []
    assert preview["files"] == sorted([str(shared / "甲.mkv"), str(shared / "字幕" / "甲.srt")])
    assert preview["file_count"] == 2

    response = _delete(client, mine, preview)
    assert response.status_code == 200, response.text
    assert not (shared / "甲.mkv").exists()
    assert not (shared / "字幕").exists()
    assert (shared / "乙.mkv").exists()
    assert _gone(app, mine)
    assert not _gone(app, other)
    assert _file_paths(app) == {str(shared / "乙.mkv")}


def test_unmatched_file_in_folder_is_not_exclusive(
    client: TestClient, app: FastAPI, root: Path
) -> None:
    library = _library(client, root)
    item = _item(app, library)
    folder = root / "教父"
    _file(app, library, item, folder / "教父.mkv")
    _file(app, library, None, folder / "没认出.mkv")

    preview = _preview(client, item)
    assert preview["folders"] == []
    assert preview["files"] == [str(folder / "教父.mkv")]
    assert _delete(client, item, preview).status_code == 200
    assert (folder / "没认出.mkv").exists()


def test_files_in_library_root(client: TestClient, app: FastAPI, root: Path) -> None:
    library = _library(client, root)
    item = _item(app, library)
    _file(app, library, item, root / "教父.mkv")
    _file(app, library, item, root / "教父.nfo", "nfo")
    (root / "别的.txt").write_bytes(b"keep")

    preview = _preview(client, item)
    assert preview["folders"] == []
    assert preview["files"] == sorted([str(root / "教父.mkv"), str(root / "教父.nfo")])

    assert _delete(client, item, preview).status_code == 200
    assert root.is_dir()
    assert (root / "别的.txt").exists()
    assert not (root / "教父.mkv").exists()


def test_galgame_deletes_game_folder(client: TestClient, app: FastAPI, tmp_path: Path) -> None:
    root = tmp_path / "games"
    library = _library(client, root, "galgame")
    item = _item(app, library, "千恋万花", data_type="galgame")
    game = root / "品牌" / "千恋万花"
    _file(app, library, item, game, "game")
    (game / "game.exe").write_bytes(b"exe")
    (game / "save").mkdir()
    (game / "save" / "01.sav").write_bytes(b"sv")
    (root / "品牌" / "别的游戏").mkdir()

    preview = _preview(client, item)
    assert preview["folders"] == [str(game)]
    assert preview["files"] == []
    assert preview["file_count"] == 2
    assert preview["total_size"] == 5

    assert _delete(client, item, preview).status_code == 200
    assert not game.exists()
    assert (root / "品牌" / "别的游戏").is_dir()
    assert _gone(app, item)


def test_outside_library_rejected_without_deleting(
    client: TestClient, app: FastAPI, root: Path, tmp_path: Path, trash: FakeTrash
) -> None:
    library = _library(client, root)
    item = _item(app, library)
    inside = root / "教父" / "教父.mkv"
    outside = tmp_path / "outside" / "教父.srt"
    _file(app, library, item, inside)
    _file(app, library, item, outside, "subtitle")

    response = client.get(f"/api/items/{item}/delete-preview")
    assert response.status_code == 400
    assert "库目录" in response.json()["detail"]

    body = {"confirm_title": "教父", "expected": EMPTY_EXPECTED}
    response = client.request("DELETE", f"/api/items/{item}", json=body)
    assert response.status_code == 400
    assert inside.exists()
    assert outside.exists()
    assert trash.removed == []
    assert not _gone(app, item)


def test_library_root_itself_never_deleted(
    client: TestClient, app: FastAPI, tmp_path: Path
) -> None:
    root = tmp_path / "games"
    library = _library(client, root, "galgame")
    item = _item(app, library, "游戏", data_type="galgame")
    _file(app, library, item, root, "game")
    (root / "game.exe").write_bytes(b"exe")

    assert client.get(f"/api/items/{item}/delete-preview").status_code == 400
    assert (root / "game.exe").exists()


@pytest.mark.parametrize("kind", ["collection", "series", "season", "episode"])
def test_shows_and_collections_rejected(
    client: TestClient, app: FastAPI, tmp_path: Path, kind: str
) -> None:
    if kind == "collection":
        library = _library(client, tmp_path / "movies")
        target = _item(app, library, "教父（系列）", kind="collection")
    else:
        library = _library(client, tmp_path / "tv", "tv")
        show = _item(app, library, "剧", data_type="tv")
        season = _item(app, library, "第 1 季", data_type="tv", kind="season", parent_id=show)
        episode = _item(app, library, "第 1 集", data_type="tv", kind="episode", parent_id=season)
        target = {"series": show, "season": season, "episode": episode}[kind]

    assert client.get(f"/api/items/{target}/delete-preview").status_code == 400
    body = {"confirm_title": "x", "expected": EMPTY_EXPECTED}
    assert client.request("DELETE", f"/api/items/{target}", json=body).status_code == 400
    assert not _gone(app, target)


def _task(app: FastAPI, status: str, payload: str, task_type: str = "scrape") -> int:
    with app.state.session_factory() as session:
        row = Task(task_type=task_type, status=status, payload=payload, created_at=utc_now())
        session.add(row)
        session.commit()
        return row.id


def test_running_task_blocks(client: TestClient, app: FastAPI, root: Path) -> None:
    library = _library(client, root)
    item = _item(app, library)
    _file(app, library, item, root / "教父" / "教父.mkv")
    _task(app, "running", f'{{"item_id": {item}}}')

    preview = _preview(client, item)
    response = _delete(client, item, preview)
    assert response.status_code == 409
    assert "正在处理" in response.json()["detail"]
    assert (root / "教父" / "教父.mkv").exists()
    assert not _gone(app, item)


def test_running_library_task_blocks(client: TestClient, app: FastAPI, root: Path) -> None:
    library = _library(client, root)
    item = _item(app, library)
    _file(app, library, item, root / "教父" / "教父.mkv")
    _task(app, "running", f'{{"library_id": {library}}}')

    assert _delete(client, item, _preview(client, item)).status_code == 409
    assert (root / "教父" / "教父.mkv").exists()


def _statuses(app: FastAPI, *task_ids: int) -> list[str]:
    with app.state.session_factory() as session:
        return [session.get(Task, task_id).status for task_id in task_ids]


def test_queued_tasks_cancelled_before_disk(
    client: TestClient, app: FastAPI, root: Path, trash: FakeTrash
) -> None:
    library = _library(client, root)
    item = _item(app, library)
    other = _item(app, library, "乙")
    _file(app, library, item, root / "教父" / "教父.mkv")
    scrape = _task(app, "queued", f'{{"item_id": {item}, "force": false}}')
    arrange = _task(app, "queued", f'{{"item_id": {item}}}', "arrange")
    theirs = _task(app, "queued", f'{{"item_id": {other}}}')
    seen: list[list[str]] = []
    trash.before_remove = lambda _path: seen.append(_statuses(app, scrape, arrange, theirs))

    assert _delete(client, item, _preview(client, item)).status_code == 200
    assert seen == [["cancelled", "cancelled", "queued"]]
    assert _statuses(app, scrape, arrange, theirs) == ["cancelled", "cancelled", "queued"]


def test_mismatch_does_not_cancel_tasks(client: TestClient, app: FastAPI, root: Path) -> None:
    library = _library(client, root)
    item = _item(app, library)
    _file(app, library, item, root / "教父" / "教父.mkv")
    preview = _preview(client, item)
    (root / "教父" / "新写的.txt").write_bytes(b"new")
    scrape = _task(app, "queued", f'{{"item_id": {item}}}')

    assert _delete(client, item, preview).status_code == 409
    assert _statuses(app, scrape) == ["queued"]


def test_preview_mismatch_409(client: TestClient, app: FastAPI, root: Path) -> None:
    library = _library(client, root)
    item = _item(app, library)
    shared = root / "合集"
    other = _item(app, library, "乙")
    _file(app, library, item, shared / "教父.mkv")
    _file(app, library, other, shared / "乙.mkv")
    preview = _preview(client, item)
    _file(app, library, item, shared / "教父.srt", "subtitle")

    response = _delete(client, item, preview)
    assert response.status_code == 409
    assert (shared / "教父.mkv").exists()
    assert (shared / "教父.srt").exists()
    assert not _gone(app, item)


def test_partial_failure_keeps_item(
    client: TestClient, app: FastAPI, root: Path, trash: FakeTrash
) -> None:
    library = _library(client, root)
    item = _item(app, library)
    other = _item(app, library, "乙")
    shared = root / "合集"
    video = shared / "教父.mkv"
    nfo = shared / "教父.nfo"
    _file(app, library, item, video)
    _file(app, library, item, nfo, "nfo")
    _file(app, library, other, shared / "乙.mkv")
    arrange = _task(app, "queued", f'{{"item_id": {item}}}', "arrange")
    trash.fail.add(video)

    response = _delete(client, item, _preview(client, item))
    assert response.status_code == 409
    assert str(video) in response.json()["detail"]
    assert _statuses(app, arrange) == ["cancelled"]
    assert video.exists()
    assert not nfo.exists()
    assert not _gone(app, item)
    assert _file_paths(app) == {str(video), str(shared / "乙.mkv")}

    trash.fail.clear()
    retry = _preview(client, item)
    assert retry["files"] == [str(video)]
    assert _delete(client, item, retry).status_code == 200
    assert _gone(app, item)


def test_empty_collection_dropped(client: TestClient, app: FastAPI, root: Path) -> None:
    library = _library(client, root)
    collection = _item(app, library, "教父（系列）", kind="collection")
    first = _item(app, library, "教父", parent_id=collection)
    second = _item(app, library, "教父 2", parent_id=collection)
    _file(app, library, first, root / "教父" / "教父.mkv")
    _file(app, library, second, root / "教父 2" / "教父 2.mkv")

    assert _delete(client, first, _preview(client, first)).status_code == 200
    assert not _gone(app, collection)
    assert _delete(client, second, _preview(client, second)).status_code == 200
    assert _gone(app, collection)


def test_network_drive_marked_permanent(
    client: TestClient, app: FastAPI, root: Path, trash: FakeTrash
) -> None:
    trash.permanent = True
    library = _library(client, root)
    item = _item(app, library)
    folder = root / "教父"
    _file(app, library, item, folder / "教父.mkv")
    preview = _preview(client, item)
    assert preview["permanent"] is True
    assert _delete(client, item, preview).status_code == 200
    assert trash.removed == [(folder, True)]


def test_image_cache_removed(client: TestClient, app: FastAPI, root: Path, data_dir: Path) -> None:
    images = data_dir / "images"
    for rel in (IMAGE_A, IMAGE_B, IMAGE_SHARED):
        (images / rel).parent.mkdir(parents=True, exist_ok=True)
        (images / rel).write_bytes(b"img")
    library = _library(client, root)
    item = _item(app, library, poster=IMAGE_A)
    other = _item(app, library, "乙", poster=IMAGE_SHARED)
    with app.state.session_factory() as session:
        for rel in (IMAGE_B, IMAGE_SHARED):
            session.add(ItemImage(item_id=item, image_kind="fanart", file_path=rel, position=0))
        session.commit()
    _file(app, library, item, root / "教父" / "教父.mkv")

    assert _delete(client, item, _preview(client, item)).status_code == 200
    assert not (images / IMAGE_A).exists()
    assert not (images / IMAGE_B).exists()
    assert (images / IMAGE_SHARED).exists()
    assert not _gone(app, other)


def test_wrong_title_400(client: TestClient, app: FastAPI, root: Path, trash: FakeTrash) -> None:
    library = _library(client, root)
    item = _item(app, library)
    _file(app, library, item, root / "教父" / "教父.mkv")
    preview = _preview(client, item)

    assert _delete(client, item, preview, title="教母").status_code == 400
    assert (root / "教父" / "教父.mkv").exists()
    assert trash.removed == []
    assert _delete(client, item, preview, title="  教父 ").status_code == 200


def test_missing_item_404(client: TestClient) -> None:
    assert client.get("/api/items/999/delete-preview").status_code == 404


def test_unregistered_video_blocks_exclusive(client: TestClient, app: FastAPI, root: Path) -> None:
    library = _library(client, root)
    item = _item(app, library)
    folder = root / "教父"
    _file(app, library, item, folder / "教父.mkv")
    (folder / "extras").mkdir()
    (folder / "extras" / "别的片.MKV").write_bytes(b"other")

    preview = _preview(client, item)
    assert preview["folders"] == []
    assert preview["files"] == [str(folder / "教父.mkv")]
    assert preview["unregistered"] == {"count": 0, "total_size": 0, "sample": []}
    assert _delete(client, item, preview).status_code == 200
    assert (folder / "extras" / "别的片.MKV").exists()


def test_videos_in_two_folders_not_exclusive(client: TestClient, app: FastAPI, root: Path) -> None:
    library = _library(client, root)
    item = _item(app, library)
    movie = root / "教父"
    _file(app, library, item, movie / "CD1" / "教父.cd1.mkv")
    _file(app, library, item, movie / "CD2" / "教父.cd2.mkv")
    (movie / "别的").mkdir()
    (movie / "别的" / "说明.txt").write_bytes(b"keep")

    preview = _preview(client, item)
    assert preview["folders"] == []
    assert len(preview["files"]) == 2
    assert _delete(client, item, preview).status_code == 200
    assert not (movie / "CD1").exists()
    assert not (movie / "CD2").exists()
    assert (movie / "别的" / "说明.txt").exists()


def test_galgame_with_outside_file_keeps_brand(
    client: TestClient, app: FastAPI, tmp_path: Path
) -> None:
    root = tmp_path / "games"
    library = _library(client, root, "galgame")
    item = _item(app, library, "千恋万花", data_type="galgame")
    brand = root / "品牌"
    game = brand / "千恋万花"
    _file(app, library, item, game, "game")
    (game / "game.exe").write_bytes(b"exe")
    _file(app, library, item, brand / "千恋万花.nfo", "nfo")
    (brand / "别的游戏").mkdir()
    (brand / "别的游戏" / "game.exe").write_bytes(b"exe")

    preview = _preview(client, item)
    assert preview["folders"] == [str(game)]
    assert preview["files"] == [str(brand / "千恋万花.nfo")]
    assert _delete(client, item, preview).status_code == 200
    assert not game.exists()
    assert (brand / "别的游戏" / "game.exe").exists()


def test_new_file_after_preview_409(client: TestClient, app: FastAPI, root: Path) -> None:
    library = _library(client, root)
    item = _item(app, library)
    folder = root / "教父"
    _file(app, library, item, folder / "教父.mkv")
    preview = _preview(client, item)
    assert preview["folders"] == [str(folder)]
    (folder / "新写的.txt").write_bytes(b"new")

    assert _delete(client, item, preview).status_code == 409
    assert (folder / "新写的.txt").exists()
    assert (folder / "教父.mkv").exists()


def test_queued_organize_blocks(client: TestClient, app: FastAPI, root: Path) -> None:
    library = _library(client, root)
    item = _item(app, library)
    _file(app, library, item, root / "教父" / "教父.mkv")
    preview = _preview(client, item)
    _task(app, "queued", f'{{"library_id": {library}, "scrape": null}}', "organize")

    response = _delete(client, item, preview)
    assert response.status_code == 409
    assert "正在处理" in response.json()["detail"]
    assert (root / "教父" / "教父.mkv").exists()


def test_other_library_through_junction_not_exclusive(
    client: TestClient, app: FastAPI, tmp_path: Path
) -> None:
    root = tmp_path / "movies"
    library = _library(client, root)
    item = _item(app, library)
    folder = root / "教父"
    _file(app, library, item, folder / "教父.mkv")
    alias = tmp_path / "alias"
    make_junction(alias, folder)
    other_library = _library(client, alias)
    other = _item(app, other_library, "乙")
    _file(app, other_library, other, alias / "乙.mkv")

    preview = _preview(client, item)
    assert preview["folders"] == []
    assert preview["files"] == [str(folder / "教父.mkv")]
    assert _delete(client, item, preview).status_code == 200
    assert (folder / "乙.mkv").exists()


def test_junction_inside_exclusive_folder_only_link(
    client: TestClient, app: FastAPI, root: Path, tmp_path: Path
) -> None:
    library = _library(client, root)
    item = _item(app, library)
    folder = root / "教父"
    _file(app, library, item, folder / "教父.mkv", content=b"12345")
    outside = tmp_path / "库外"
    outside.mkdir()
    (outside / "大文件.bin").write_bytes(b"x" * 1000)
    (outside / "片.mkv").write_bytes(b"y")
    make_junction(folder / "链接", outside)

    preview = _preview(client, item)
    assert preview["folders"] == [str(folder)]
    assert preview["file_count"] == 2
    assert preview["total_size"] < 1000
    assert "文件夹里有链接，只删链接本身" in preview["reasons"]

    assert _delete(client, item, preview).status_code == 200
    assert not folder.exists()
    assert (outside / "大文件.bin").exists()
    assert (outside / "片.mkv").exists()


def test_library_root_unreachable_409(client: TestClient, app: FastAPI, root: Path) -> None:
    library = _library(client, root)
    item = _item(app, library)
    _file(app, library, item, root / "教父" / "教父.mkv")
    (root / "教父" / "教父.mkv").unlink()
    (root / "教父").rmdir()
    root.rmdir()

    response = client.get(f"/api/items/{item}/delete-preview")
    assert response.status_code == 409
    assert "访问不到" in response.json()["detail"]
    assert not _gone(app, item)


def test_item_without_files_only_db(
    client: TestClient, app: FastAPI, root: Path, trash: FakeTrash
) -> None:
    library = _library(client, root)
    item = _item(app, library)
    (root / "别的.mkv").write_bytes(b"keep")

    preview = _preview(client, item)
    assert preview["folders"] == [] and preview["files"] == []
    assert preview["file_count"] == 0
    assert _delete(client, item, preview).status_code == 200
    assert _gone(app, item)
    assert trash.removed == []
    assert (root / "别的.mkv").exists()


def test_junction_to_outside_rejected(
    client: TestClient, app: FastAPI, tmp_path: Path, trash: FakeTrash
) -> None:
    root = tmp_path / "games"
    library = _library(client, root, "galgame")
    item = _item(app, library, "游戏", data_type="galgame")
    outside = tmp_path / "库外游戏"
    outside.mkdir()
    (outside / "game.exe").write_bytes(b"exe")
    link = root / "游戏"
    make_junction(link, outside)
    _file(app, library, item, link, "game")

    response = client.get(f"/api/items/{item}/delete-preview")
    assert response.status_code == 400
    body = {"confirm_title": "游戏", "expected": EMPTY_EXPECTED}
    assert client.request("DELETE", f"/api/items/{item}", json=body).status_code == 400
    assert (outside / "game.exe").exists()
    assert link.exists()
    assert trash.removed == []


def test_file_through_junction_rejected(
    client: TestClient, app: FastAPI, root: Path, tmp_path: Path
) -> None:
    library = _library(client, root)
    item = _item(app, library)
    outside = tmp_path / "库外"
    outside.mkdir()
    make_junction(root / "链接", outside)
    _file(app, library, item, root / "链接" / "教父.mkv")

    assert client.get(f"/api/items/{item}/delete-preview").status_code == 400
    assert (outside / "教父.mkv").exists()


def test_unreadable_subfolder_not_exclusive(
    client: TestClient, app: FastAPI, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    library = _library(client, root)
    item = _item(app, library)
    folder = root / "教父"
    _file(app, library, item, folder / "教父.mkv")
    locked = folder / "读不了"
    locked.mkdir()
    (locked / "说明.txt").write_bytes(b"keep")
    original = os.scandir

    def scandir(path: Any = ".") -> Any:
        if os.path.normcase(os.fspath(path)) == os.path.normcase(str(locked)):
            raise PermissionError(f"拒绝访问：{path}")
        return original(path)

    monkeypatch.setattr(os, "scandir", scandir)
    preview = _preview(client, item)
    assert preview["folders"] == []
    assert preview["files"] == [str(folder / "教父.mkv")]
    assert _delete(client, item, preview).status_code == 200
    assert (locked / "说明.txt").exists()


def test_junction_inside_library_not_exclusive(
    client: TestClient, app: FastAPI, root: Path
) -> None:
    library = _library(client, root)
    item = _item(app, library)
    real = root / "真身"
    real.mkdir()
    make_junction(root / "链接", real)
    folder = root / "链接" / "教父"
    _file(app, library, item, folder / "教父.mkv")
    (folder / "说明.txt").write_bytes(b"keep")

    preview = _preview(client, item)
    assert preview["folders"] == []
    assert preview["files"] == [str(folder / "教父.mkv")]
    assert _delete(client, item, preview).status_code == 200
    assert not (real / "教父" / "教父.mkv").exists()
    assert (real / "教父" / "说明.txt").exists()


def test_nested_library_root_not_exclusive(client: TestClient, app: FastAPI, root: Path) -> None:
    library = _library(client, root)
    item = _item(app, library)
    folder = root / "教父"
    _file(app, library, item, folder / "教父.mkv")
    nested = folder / "另一个库"
    nested.mkdir()
    (nested / "说明.txt").write_bytes(b"keep")
    with app.state.session_factory() as session:
        other = Library(
            data_type_id="movie", name="嵌套", created_at=utc_now(), updated_at=utc_now()
        )
        other.paths = [LibraryPath(path=str(nested))]
        session.add(other)
        session.commit()

    preview = _preview(client, item)
    assert preview["folders"] == []
    assert preview["files"] == [str(folder / "教父.mkv")]
    assert _delete(client, item, preview).status_code == 200
    assert (nested / "说明.txt").exists()
