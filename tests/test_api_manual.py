from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from mortis.api.app import create_app
from mortis.config.models import Secrets
from mortis.db.models import Item, Library, MediaFile, Task, utc_now
from mortis.items.edit import ItemError
from mortis.items.identity import ExternalRef, MovieRef, create_manual_work
from mortis.items.matching import bind_file, bind_folder, bind_manual
from mortis.organize.service import unscraped_work_ids
from mortis.output.service import arrangeable_items
from mortis.settings import Settings
from tests.fakes import FakeTmdb

LOCAL = "http://127.0.0.1"


@pytest.fixture
def app(tmp_path: Path) -> FastAPI:
    fake = FakeTmdb()
    heat = {"id": 949, "title": "盗火线", "original_title": "Heat", "release_date": "1995-12-15"}
    fake.add("/3/movie/949", heat)
    app = create_app(
        Settings(data_dir=tmp_path / "data"), http_transport=fake.transport, start_worker=False
    )
    app.state.config_store.save_secrets(Secrets(sources={"tmdb": {"api_key": "k"}}))
    return app


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app, base_url=LOCAL) as opened:
        yield opened


def _library(client: TestClient, root: Path, data_type: str) -> int:
    root.mkdir(parents=True, exist_ok=True)
    body = {"name": root.name, "data_type_id": data_type, "paths": [str(root)]}
    response = client.post("/api/libraries", json=body)
    assert response.status_code in (200, 201), response.text
    return response.json()["id"]


def _add_files(app: FastAPI, library_id: int, paths: dict[str, tuple[Path, str]]) -> dict[str, int]:
    now = utc_now()
    ids: dict[str, int] = {}
    with app.state.session_factory() as session:
        for key, (path, kind) in paths.items():
            if kind == "game":
                path.mkdir(parents=True, exist_ok=True)
            else:
                path.write_bytes(b"x")
            row = MediaFile(
                library_id=library_id,
                path=str(path),
                file_kind=kind,
                size=1,
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            session.flush()
            ids[key] = row.id
        session.commit()
    return ids


@pytest.fixture
def files(app: FastAPI, client: TestClient, tmp_path: Path) -> dict[str, int]:
    root = tmp_path / "电影"
    folder = root / "B站下载 2024"
    folder.mkdir(parents=True)
    library = _library(client, root, "movie")
    ids = _add_files(
        app,
        library,
        {
            "cd1": (folder / "video.cd1.mp4", "video"),
            "cd2": (folder / "video.cd2.mp4", "video"),
            "sub": (folder / "video.srt", "subtitle"),
            "loose": (root / "Other.2001.mkv", "video"),
        },
    )
    return {"library": library, **ids}


def _names(rows: list[dict]) -> list[str]:
    return [Path(r["path"]).name for r in rows]


def _field(detail: dict[str, Any], key: str) -> dict[str, Any]:
    for section in detail["sections"]:
        for field in section["fields"]:
            if field["key"] == key:
                return field
    raise AssertionError(f"没有字段 {key}")


def _wall(client: TestClient, library_id: int) -> dict[int, dict[str, Any]]:
    rows = client.get(f"/api/libraries/{library_id}/items").json()
    return {row["id"]: row for row in rows}


def test_manual_movie_takes_parts_and_locks_filled_fields(
    client: TestClient, app: FastAPI, files: dict[str, int]
) -> None:
    body = {"title": "  某个 B 站视频 ", "overview": "自己写的简介"}
    response = client.post(f"/api/media-files/{files['cd1']}/manual", json=body)
    assert response.status_code == 200, response.text
    item_id = response.json()["item_id"]

    with app.state.session_factory() as session:
        for key in ("cd1", "cd2", "sub"):
            assert session.get(MediaFile, files[key]).item_id == item_id
        assert session.get(MediaFile, files["loose"]).item_id is None
    assert _names(client.get("/api/media-files/unmatched").json()) == ["Other.2001.mkv"]

    detail = client.get(f"/api/items/{item_id}").json()
    assert detail["is_manual"] is True
    assert detail["title"] == "某个 B 站视频"
    assert detail["external_ids"] == {}
    for key in ("title", "overview"):
        field = _field(detail, key)
        assert (field["source"], field["locked"]) == ("manual", True), key
    assert _field(detail, "overview")["value"] == "自己写的简介"
    release = _field(detail, "release_date")
    assert (release["value"], release["source"], release["locked"]) == (None, None, False)
    with app.state.session_factory() as session:
        assert session.scalars(select(Task).where(Task.task_type == "scrape")).all() == []


def test_wall_marks_manual_items(client: TestClient, files: dict[str, int]) -> None:
    manual = client.post(
        f"/api/media-files/{files['cd1']}/manual",
        json={"title": "手动片", "release_date": "2024-05-01"},
    ).json()["item_id"]
    bound = client.post(
        f"/api/media-files/{files['loose']}/bind", json={"tmdb_id": 949, "title": "盗火线"}
    ).json()["item_id"]
    wall = _wall(client, files["library"])
    assert (wall[manual]["is_manual"], wall[manual]["is_scraped"]) == (True, False)
    assert wall[manual]["year"] == 2024
    assert wall[bound]["is_manual"] is False
    assert client.get(f"/api/items/{bound}").json()["is_manual"] is False


def test_library_counts_manual_apart_from_pending(
    client: TestClient, app: FastAPI, files: dict[str, int]
) -> None:
    manual = client.post(
        f"/api/media-files/{files['cd1']}/manual", json={"title": "手动片"}
    ).json()["item_id"]
    bound = client.post(
        f"/api/media-files/{files['loose']}/bind", json={"tmdb_id": 949, "title": "盗火线"}
    ).json()["item_id"]
    library_id = files["library"]
    detail = client.get(f"/api/libraries/{library_id}").json()
    listed = next(row for row in client.get("/api/libraries").json() if row["id"] == library_id)
    for row in (detail, listed):
        assert (row["item_count"], row["scraped_count"], row["manual_count"]) == (2, 0, 1)
    with app.state.session_factory() as session:
        queued = unscraped_work_ids(session, library_id)
    assert queued == [bound]
    assert manual not in queued
    assert len(queued) == detail["item_count"] - detail["scraped_count"] - detail["manual_count"]


def test_manual_item_refuses_scrape_until_rematched(
    client: TestClient, files: dict[str, int]
) -> None:
    item_id = client.post(
        f"/api/media-files/{files['cd1']}/manual", json={"title": "手动片"}
    ).json()["item_id"]
    response = client.post(f"/api/items/{item_id}/scrape")
    assert response.status_code == 422
    assert response.json()["detail"] == "手动条目没有来源编号，要刮削请先重新匹配"

    rematched = client.post(f"/api/items/{item_id}/rematch", json={"tmdb_id": 949})
    assert rematched.status_code == 202, rematched.text
    assert client.get(f"/api/items/{item_id}").json()["is_manual"] is False
    assert _wall(client, files["library"])[item_id]["is_manual"] is False


def test_manual_rejects_bound_file_and_blank_title(
    client: TestClient, files: dict[str, int]
) -> None:
    subtitle = client.post(f"/api/media-files/{files['sub']}/manual", json={"title": "x"})
    assert subtitle.status_code == 422
    assert subtitle.json()["detail"] == "只能给视频文件或游戏目录建条目"
    first = client.post(f"/api/media-files/{files['cd1']}/manual", json={"title": "手动片"})
    assert first.status_code == 200
    again = client.post(f"/api/media-files/{files['cd2']}/manual", json={"title": "又一个"})
    assert again.status_code == 409
    blank = client.post(f"/api/media-files/{files['loose']}/manual", json={"title": "  "})
    assert blank.status_code == 422
    assert blank.json()["detail"] == "片名不能为空"
    missing = client.post("/api/media-files/99999/manual", json={"title": "x"})
    assert missing.status_code == 404


def test_manual_galgame_binds_only_the_folder(
    client: TestClient, app: FastAPI, tmp_path: Path
) -> None:
    root = tmp_path / "游戏"
    library = _library(client, root, "galgame")
    ids = _add_files(
        app,
        library,
        {"game": (root / "同人小游戏", "game"), "other": (root / "另一个游戏", "game")},
    )
    response = client.post(
        f"/api/media-files/{ids['game']}/manual",
        json={"title": "同人小游戏", "original_title": "Doujin", "overview": "简介"},
    )
    assert response.status_code == 200, response.text
    item_id = response.json()["item_id"]
    detail = client.get(f"/api/items/{item_id}").json()
    assert detail["is_manual"] is True
    assert detail["data_type_id"] == "galgame"
    assert _field(detail, "original_title")["source"] == "manual"
    assert _names(client.get("/api/media-files/unmatched").json()) == ["另一个游戏"]
    with app.state.session_factory() as session:
        assert session.get(MediaFile, ids["other"]).item_id is None
    scrape = client.post(f"/api/items/{item_id}/scrape")
    assert scrape.status_code == 422
    assert scrape.json()["detail"] == "手动条目没有来源编号，要刮削请先重新匹配"


def _episodes(detail: dict[str, Any]) -> list[tuple[int, int, str, bool]]:
    return [
        (season["number"], episode["number"], episode["title"], episode["has_file"])
        for season in detail["seasons"]
        for episode in season["episodes"]
    ]


def _manual(client: TestClient, file_id: int, title: str) -> int:
    response = client.post(f"/api/media-files/{file_id}/manual", json={"title": title})
    assert response.status_code == 200, response.text
    return response.json()["item_id"]


def _owner(app: FastAPI, file_id: int) -> tuple[str, int | None, str]:
    """文件挂在哪：(条目种类, 季号或集号, 标题)。"""
    with app.state.session_factory() as session:
        row = session.get(MediaFile, file_id)
        item = session.get(Item, row.item_id)
        return item.kind, item.index_number, item.title


def test_manual_show_builds_seasons_and_episodes_from_numbers(
    client: TestClient, app: FastAPI, tmp_path: Path
) -> None:
    root = tmp_path / "剧"
    show = root / "某剧"
    (show / "Season 2").mkdir(parents=True)
    (root / "另一部").mkdir()
    library = _library(client, root, "tv")
    ids = _add_files(
        app,
        library,
        {
            "e1": (show / "Show.S01E01.mkv", "video"),
            "e2": (show / "Show.S01E02.mkv", "video"),
            "sub": (show / "Show.S01E01.zh.srt", "subtitle"),
            "s2": (show / "Season 2" / "Show.S02E01.mkv", "video"),
            "other": (root / "另一部" / "a.mkv", "video"),
        },
    )
    response = client.post(f"/api/media-files/{ids['e2']}/manual", json={"title": "某剧"})
    assert response.status_code == 200, response.text
    item_id = response.json()["item_id"]

    assert _owner(app, ids["e1"]) == ("episode", 1, "Show.S01E01")
    assert _owner(app, ids["sub"]) == ("episode", 1, "Show.S01E01")
    assert _owner(app, ids["e2"]) == ("episode", 2, "Show.S01E02")
    assert _owner(app, ids["s2"]) == ("episode", 1, "Show.S02E01")
    assert _names(client.get("/api/media-files/unmatched").json()) == ["另一部"]

    detail = client.get(f"/api/items/{item_id}").json()
    assert detail["is_manual"] is True
    assert [(s["number"], s["title"]) for s in detail["seasons"]] == [
        (1, "第 1 季"),
        (2, "第 2 季"),
    ]
    assert _episodes(detail) == [
        (1, 1, "Show.S01E01", True),
        (1, 2, "Show.S01E02", True),
        (2, 1, "Show.S02E01", True),
    ]
    season = client.get(f"/api/items/{detail['seasons'][0]['id']}").json()
    assert season["is_manual"] is True
    assert season["show"]["id"] == item_id
    assert [s["number"] for s in season["seasons"]] == [1, 2]
    episode_id = detail["seasons"][0]["episodes"][0]["id"]
    episode = client.get(f"/api/items/{episode_id}").json()
    assert episode["position"] == "第 1 季 第 1 集"
    title = _field(episode, "title")
    assert (title["source"], title["locked"]) == (None, False)


def test_manual_show_without_numbers_counts_by_path(
    client: TestClient, app: FastAPI, tmp_path: Path
) -> None:
    root = tmp_path / "剧"
    show = root / "B站合集"
    show.mkdir(parents=True)
    library = _library(client, root, "tv")
    ids = _add_files(
        app,
        library,
        {
            "c": (show / "P3 结尾.mp4", "video"),
            "a": (show / "P1 开场.mp4", "video"),
            "b": (show / "P2 正片.mp4", "video"),
            "sub": (show / "P2 正片.srt", "subtitle"),
        },
    )
    item_id = _manual(client, ids["c"], "B站合集")

    assert _episodes(client.get(f"/api/items/{item_id}").json()) == [
        (1, 1, "P1 开场", True),
        (1, 2, "P2 正片", True),
        (1, 3, "P3 结尾", True),
    ]
    assert _owner(app, ids["sub"]) == ("episode", 2, "P2 正片")


def test_manual_show_counts_parts_in_natural_order(
    client: TestClient, app: FastAPI, tmp_path: Path
) -> None:
    root = tmp_path / "剧"
    show = root / "多P合集"
    show.mkdir(parents=True)
    library = _library(client, root, "tv")
    names = ["P1 开场.mp4", "P2 正片.mp4", "p10 番外.mp4", "P12 结尾.mp4"]
    ids = _add_files(app, library, {name: (show / name, "video") for name in reversed(names)})
    _manual(client, ids[names[0]], "多P合集")

    for number, name in enumerate(names, start=1):
        assert _owner(app, ids[name]) == ("episode", number, Path(name).stem)


@pytest.mark.parametrize(
    "names",
    [
        ("A.E01.mkv", "B.E01.mkv", "C.E02.mkv"),
        ("A.S01E01.mkv", "B.第1集.mkv", "C.S01E02.mkv"),
        ("A.S01E01.mkv", "B 没有集号.mkv", "C.S01E02.mkv"),
    ],
)
def test_manual_show_falls_back_when_numbers_clash_or_missing(
    client: TestClient, app: FastAPI, tmp_path: Path, names: tuple[str, str, str]
) -> None:
    root = tmp_path / "剧"
    show = root / "合集"
    show.mkdir(parents=True)
    library = _library(client, root, "tv")
    ids = _add_files(app, library, {name: (show / name, "video") for name in names})
    item_id = _manual(client, ids[names[0]], "合集")

    detail = client.get(f"/api/items/{item_id}").json()
    assert _episodes(detail) == [
        (1, number, Path(name).stem, True) for number, name in enumerate(names, start=1)
    ]


def test_manual_show_in_library_root_takes_only_that_file(
    client: TestClient, app: FastAPI, tmp_path: Path
) -> None:
    root = tmp_path / "剧"
    library = _library(client, root, "tv")
    ids = _add_files(
        app,
        library,
        {
            "one": (root / "Loose.S01E03.mkv", "video"),
            "two": (root / "Other.S01E01.mkv", "video"),
        },
    )
    item_id = _manual(client, ids["one"], "散装")
    assert _episodes(client.get(f"/api/items/{item_id}").json()) == [(1, 3, "Loose.S01E03", True)]
    with app.state.session_factory() as session:
        assert session.get(MediaFile, ids["two"]).item_id is None


def test_manual_anime_lands_in_anime_library(
    client: TestClient, app: FastAPI, tmp_path: Path
) -> None:
    root = tmp_path / "动漫"
    show = root / "[字幕组] 某番"
    show.mkdir(parents=True)
    library = _library(client, root, "anime")
    ids = _add_files(
        app,
        library,
        {
            "e1": (show / "[字幕组] 某番 [01].mkv", "video"),
            "e2": (show / "[字幕组] 某番 [02].mkv", "video"),
        },
    )
    response = client.post(f"/api/media-files/{ids['e1']}/manual", json={"title": "某番"})
    assert response.status_code == 200, response.text
    detail = client.get(f"/api/items/{response.json()['item_id']}").json()
    assert (detail["data_type_id"], detail["library_id"], detail["is_manual"]) == (
        "anime",
        library,
        True,
    )
    assert _episodes(detail) == [
        (1, 1, "[字幕组] 某番 [01]", True),
        (1, 2, "[字幕组] 某番 [02]", True),
    ]


_ID_TAGS = ("<uniqueid", "<tmdbid", "<imdbid", "<tvdbid", "<id>", "<id/>", "<episodeguide")


def _no_ids(nfo: Path) -> str:
    text = nfo.read_text(encoding="utf-8")
    for tag in _ID_TAGS:
        assert tag not in text, (nfo.name, tag)
    return text


def _rename_movies(app: FastAPI) -> None:
    config = app.state.config_store.load_config().types.movie
    config.output.organize_rename = True
    app.state.config_store.save_section("types.movie", config)


def test_manual_movie_arranges_with_name_and_nfo(
    client: TestClient, app: FastAPI, files: dict[str, int], tmp_path: Path
) -> None:
    _rename_movies(app)
    body = {"title": "手动片", "release_date": "2024-05-01", "overview": "自己写的简介"}
    item_id = client.post(f"/api/media-files/{files['cd1']}/manual", json=body).json()["item_id"]
    preview = client.get(f"/api/items/{item_id}/arrange-preview")
    assert preview.status_code == 200, preview.text
    assert preview.json()["problems"] == []
    assert client.post(f"/api/items/{item_id}/arrange").status_code == 202
    app.state.task_runner.run_until_idle()

    folder = tmp_path / "电影" / "手动片 (2024)"
    assert {p.name for p in folder.iterdir()} >= {
        "手动片.cd1.mp4",
        "手动片.cd2.mp4",
        "手动片.srt",
        "movie.nfo",
    }
    assert not (tmp_path / "电影" / "B站下载 2024").exists()
    text = _no_ids(folder / "movie.nfo")
    assert "<title>手动片</title>" in text
    assert "<plot>自己写的简介</plot>" in text
    assert "<year>2024</year>" in text

    client.patch(f"/api/items/{item_id}/fields", json={"values": {"overview": "改过的简介"}})
    app.state.task_runner.run_until_idle()
    assert "<plot>改过的简介</plot>" in (folder / "movie.nfo").read_text(encoding="utf-8")
    assert (folder / "手动片.cd1.mp4").is_file()


def test_library_arrange_counts_manual_not_unscraped(
    client: TestClient, app: FastAPI, files: dict[str, int], tmp_path: Path
) -> None:
    _rename_movies(app)
    manual = client.post(
        f"/api/media-files/{files['cd1']}/manual",
        json={"title": "手动片", "release_date": "2024-05-01"},
    ).json()["item_id"]
    bound = client.post(
        f"/api/media-files/{files['loose']}/bind", json={"tmdb_id": 949, "title": "盗火线"}
    ).json()["item_id"]
    refused = client.get(f"/api/items/{bound}/arrange-preview")
    assert refused.status_code == 422
    assert "还没刮削" in refused.json()["detail"]

    listed = client.get(f"/api/libraries/{files['library']}/arrange-preview").json()
    assert listed["count"] == 1
    assert [row["item_id"] for row in listed["previews"]] == [manual]
    with app.state.session_factory() as session:
        config = app.state.config_store.load_config()
        assert arrangeable_items(session, files["library"], config) == [manual]


def test_manual_movie_without_date_has_no_empty_brackets(
    client: TestClient, app: FastAPI, files: dict[str, int], tmp_path: Path
) -> None:
    _rename_movies(app)
    item_id = _manual(client, files["cd1"], "手动片")
    preview = client.get(f"/api/items/{item_id}/arrange-preview").json()
    assert preview["folder_to"] == str(tmp_path / "电影" / "手动片")
    assert client.post(f"/api/items/{item_id}/arrange").status_code == 202
    app.state.task_runner.run_until_idle()
    assert (tmp_path / "电影" / "手动片" / "手动片.cd1.mp4").is_file()


def test_library_arrange_ignores_shows_in_other_libraries(
    client: TestClient, app: FastAPI, tmp_path: Path
) -> None:
    """一个库里没有视频的作品，不因为另一个库有视频挂在集上的剧就被算进去。"""
    movies = _library(client, tmp_path / "电影", "movie")
    with app.state.session_factory() as session:
        library = session.get(Library, movies)
        empty = create_manual_work(session, library, "没有视频")
        session.commit()
        empty_id = empty.id
    root = tmp_path / "剧"
    show = root / "某剧"
    show.mkdir(parents=True)
    shows = _library(client, root, "tv")
    ids = _add_files(app, shows, {"e1": (show / "Show.S01E01.mkv", "video")})
    show_id = _manual(client, ids["e1"], "某剧")
    with app.state.session_factory() as session:
        config = app.state.config_store.load_config()
        assert empty_id not in arrangeable_items(session, movies, config)
        assert arrangeable_items(session, movies, config) == []
        assert arrangeable_items(session, shows, config) == [show_id]
    assert client.get(f"/api/libraries/{movies}/arrange-preview").json()["count"] == 0


def test_manual_galgame_is_not_arranged(client: TestClient, app: FastAPI, tmp_path: Path) -> None:
    root = tmp_path / "游戏"
    library = _library(client, root, "galgame")
    ids = _add_files(app, library, {"game": (root / "同人小游戏", "game")})
    item_id = _manual(client, ids["game"], "同人小游戏")
    preview = client.get(f"/api/items/{item_id}/arrange-preview")
    assert preview.status_code == 422
    assert preview.json()["detail"] == "这种类型不整理"
    assert client.get(f"/api/libraries/{library}/arrange-preview").json()["count"] == 0
    assert sorted(p.name for p in root.rglob("*")) == ["同人小游戏"]


def test_plugin_type_with_video_is_not_arranged(
    client: TestClient, app: FastAPI, tmp_path: Path
) -> None:
    root = tmp_path / "游戏"
    library = _library(client, root, "galgame")
    (root / "某游戏").mkdir(parents=True)
    ids = _add_files(app, library, {"op": (root / "某游戏" / "op.mp4", "video")})
    item_id = _manual(client, ids["op"], "某游戏")
    for response in (
        client.get(f"/api/items/{item_id}/arrange-preview"),
        client.post(f"/api/items/{item_id}/arrange"),
    ):
        assert response.status_code == 422
        assert response.json()["detail"] == "这种类型不整理"
    assert client.get(f"/api/libraries/{library}/arrange-preview").json()["count"] == 0
    with app.state.session_factory() as session:
        config = app.state.config_store.load_config()
        assert arrangeable_items(session, library, config) == []


def test_manual_show_arranges_and_writes_nfo(
    client: TestClient, app: FastAPI, tmp_path: Path
) -> None:
    root = tmp_path / "剧"
    show = root / "某剧"
    show.mkdir(parents=True)
    library = _library(client, root, "tv")
    ids = _add_files(
        app,
        library,
        {
            "e1": (show / "第一集.S01E01.mkv", "video"),
            "e2": (show / "第二集.S01E02.mkv", "video"),
            "sub": (show / "第一集.S01E01.zh.srt", "subtitle"),
        },
    )
    body = {"title": "某剧", "release_date": "2020-01-01", "overview": "剧的简介"}
    item_id = client.post(f"/api/media-files/{ids['e1']}/manual", json=body).json()["item_id"]
    preview = client.get(f"/api/items/{item_id}/arrange-preview")
    assert preview.status_code == 200, preview.text
    assert preview.json()["problems"] == []
    listed = client.get(f"/api/libraries/{library}/arrange-preview").json()
    assert listed["count"] == 1
    assert client.post(f"/api/items/{item_id}/arrange").status_code == 202
    app.state.task_runner.run_until_idle()

    folder = root / "某剧 (2020)"
    names = {p.relative_to(folder).as_posix() for p in folder.rglob("*")}
    assert {
        "tvshow.nfo",
        "Season 1/season.nfo",
        "Season 1/某剧.S01E01.第一集.S01E01.mkv",
        "Season 1/某剧.S01E01.第一集.S01E01.zh.srt",
        "Season 1/某剧.S01E01.第一集.S01E01.nfo",
        "Season 1/某剧.S01E02.第二集.S01E02.mkv",
        "Season 1/某剧.S01E02.第二集.S01E02.nfo",
    } <= names
    assert not show.exists()
    tvshow = _no_ids(folder / "tvshow.nfo")
    assert "<title>某剧</title>" in tvshow and "<plot>剧的简介</plot>" in tvshow
    assert "<seasonnumber>1</seasonnumber>" in _no_ids(folder / "Season 1" / "season.nfo")
    episode = _no_ids(folder / "Season 1" / "某剧.S01E01.第一集.S01E01.nfo")
    assert "<title>第一集.S01E01</title>" in episode

    client.patch(f"/api/items/{item_id}/fields", json={"values": {"overview": "改过"}})
    app.state.task_runner.run_until_idle()
    assert "<plot>改过</plot>" in (folder / "tvshow.nfo").read_text(encoding="utf-8")


def test_rematch_manual_show_drops_built_episodes_and_keeps_locked_fields(
    client: TestClient, app: FastAPI, tmp_path: Path
) -> None:
    root = tmp_path / "剧"
    show = root / "某剧"
    show.mkdir(parents=True)
    library = _library(client, root, "tv")
    ids = _add_files(
        app,
        library,
        {name: (show / f"Show.S01E0{name}.mkv", "video") for name in ("1", "2", "3")},
    )
    body = {"title": "手填的剧名", "overview": "手填的简介"}
    item_id = client.post(f"/api/media-files/{ids['1']}/manual", json=body).json()["item_id"]
    with app.state.session_factory() as session:
        built = list(session.scalars(select(Item.id).where(Item.parent_id == item_id)))
    assert len(built) == 1

    response = client.post(f"/api/items/{item_id}/rematch", json={"tmdb_id": 1399})
    assert response.status_code == 202, response.text

    detail = client.get(f"/api/items/{item_id}").json()
    assert detail["is_manual"] is False
    assert detail["external_ids"] == {"tmdb": "1399"}
    assert detail["seasons"] == []
    for key, value in (("title", "手填的剧名"), ("overview", "手填的简介")):
        field = _field(detail, key)
        assert (field["value"], field["source"], field["locked"]) == (value, "manual", True), key
    with app.state.session_factory() as session:
        assert session.scalars(select(Item).where(Item.parent_id == item_id)).all() == []
        children = select(Item.id).where(Item.parent_id.in_(built))
        assert session.scalars(select(Item).where(Item.id.in_(children))).all() == []
        for file_id in ids.values():
            assert session.get(MediaFile, file_id).item_id == item_id


def _item_count(app: FastAPI) -> int:
    with app.state.session_factory() as session:
        return len(session.scalars(select(Item.id)).all())


def test_manual_refuses_file_bound_by_another_session(
    client: TestClient, app: FastAPI, files: dict[str, int]
) -> None:
    with app.state.session_factory() as racing:
        media_file = racing.get(MediaFile, files["cd1"])
        assert media_file.item_id is None
        winner = _manual(client, files["cd1"], "先到的")
        count = _item_count(app)
        with pytest.raises(ItemError) as error:
            bind_manual(racing, media_file, {"title": "后到的"}, app.state.services.locks)
        assert (error.value.status_code, error.value.message) == (409, "这个文件已经匹配过了")
    assert _item_count(app) == count
    assert _owner(app, files["cd1"])[2] == "先到的"
    with app.state.session_factory() as session:
        assert session.get(MediaFile, files["cd1"]).item_id == winner


def test_bind_refuses_file_bound_by_another_session(
    client: TestClient, app: FastAPI, files: dict[str, int]
) -> None:
    with app.state.session_factory() as racing:
        media_file = racing.get(MediaFile, files["loose"])
        winner = _manual(client, files["loose"], "先到的")
        count = _item_count(app)
        ref = MovieRef(tmdb_id=949, title="盗火线")
        with pytest.raises(ItemError) as error:
            bind_file(racing, media_file, ref, app.state.services.locks)
        assert (error.value.status_code, error.value.message) == (409, "这个文件已经匹配过了")
    assert _item_count(app) == count
    with app.state.session_factory() as session:
        assert session.get(MediaFile, files["loose"]).item_id == winner


def test_game_folder_bound_by_another_session_is_not_overwritten(
    client: TestClient, app: FastAPI, tmp_path: Path
) -> None:
    root = tmp_path / "游戏"
    library = _library(client, root, "galgame")
    ids = _add_files(app, library, {"game": (root / "同人小游戏", "game")})
    locks = app.state.services.locks
    with app.state.session_factory() as racing:
        media_file = racing.get(MediaFile, ids["game"])
        winner = _manual(client, ids["game"], "先到的")
        count = _item_count(app)
        with pytest.raises(ItemError) as manual:
            bind_manual(racing, media_file, {"title": "后到的"}, locks)
        assert (manual.value.status_code, manual.value.message) == (409, "这个文件已经匹配过了")
        ref = ExternalRef(provider="vndb", external_id="v1", title="后到的")
        with pytest.raises(ItemError) as folder:
            bind_folder(racing, media_file, ref, locks)
        assert (folder.value.status_code, folder.value.message) == (409, "这个目录已经匹配过了")
    assert _item_count(app) == count
    with app.state.session_factory() as session:
        assert session.get(MediaFile, ids["game"]).item_id == winner


def test_manual_refuses_file_deleted_by_another_session(
    client: TestClient, app: FastAPI, files: dict[str, int]
) -> None:
    with app.state.session_factory() as racing:
        media_file = racing.get(MediaFile, files["loose"])
        with app.state.session_factory() as other:
            other.delete(other.get(MediaFile, files["loose"]))
            other.commit()
        count = _item_count(app)
        with pytest.raises(ItemError) as error:
            bind_manual(racing, media_file, {"title": "x"}, app.state.services.locks)
        assert (error.value.status_code, error.value.message) == (404, "没有这个文件")
    assert _item_count(app) == count
