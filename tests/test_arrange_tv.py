# ruff: noqa: F401, F811  # 共用 test_scrape_tv 的夹具
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from mortis.db.models import Item, MediaFile
from mortis.rename.template import render
from mortis.rename.tv_plan import _episode_numbers
from tests.test_scrape_tv import EPISODE, _run, _scrape, app, fake, root, show_id

SHOW = "毒枭：墨西哥 (2018)"


def _client(app: FastAPI) -> TestClient:
    return TestClient(app, base_url="http://127.0.0.1")


def test_arrange_renames_show_seasons_and_episodes(app: FastAPI, show_id: int, root: Path) -> None:
    _scrape(app, show_id)
    with _client(app) as client:
        preview = client.get(f"/api/items/{show_id}/arrange-preview").json()
        assert preview["problems"] == []
        assert preview["folder_to"] == str(root / SHOW)
        targets = {Path(m["target"]).relative_to(root / SHOW).as_posix() for m in preview["moves"]}
        assert "Season 1/毒枭：墨西哥.S01E01.卡美洛.mp4" in targets
        assert client.post(f"/api/items/{show_id}/arrange").status_code == 202
    app.state.task_runner.run_until_idle()

    folder = root / SHOW
    names = {p.relative_to(folder).as_posix() for p in folder.rglob("*")}
    assert {
        "tvshow.nfo",
        "花絮.mp4",
        "season01-poster.jpg",
        "Season 1/season.nfo",
        "Season 1/毒枭：墨西哥.S01E01.卡美洛.mp4",
        "Season 1/毒枭：墨西哥.S01E01.卡美洛.chs.ass",
        "Season 1/毒枭：墨西哥.S01E01.卡美洛.nfo",
        "Season 1/毒枭：墨西哥.S01E01.卡美洛-thumb.jpg",
        "Season 2/毒枭：墨西哥.S02E03.二季3.mp4",
    } <= names
    assert not any(name.startswith(("S01", "S02")) for name in names)
    assert not (root / "毒枭：墨西哥").exists()
    with app.state.session_factory() as session:
        paths = list(session.scalars(select(MediaFile.path)))
    assert all(Path(path).exists() for path in paths)
    assert all(Path(path).is_relative_to(folder) for path in paths)

    with _client(app) as client:
        again = client.get(f"/api/items/{show_id}/arrange-preview").json()
    assert (again["changes"], again["problems"]) == (False, [])


def test_existing_target_folder_blocks_whole_show(app: FastAPI, show_id: int, root: Path) -> None:
    _scrape(app, show_id)
    (root / SHOW).mkdir()
    with _client(app) as client:
        preview = client.get(f"/api/items/{show_id}/arrange-preview").json()
        assert preview["problems"] == [f"目标目录已存在：{root / SHOW}"]
        assert client.post(f"/api/items/{show_id}/arrange").status_code == 422
    assert (root / "毒枭：墨西哥" / "S01" / f"{EPISODE.format(season=1, episode=1)}.mp4").exists()


def test_library_arrange_covers_shows(app: FastAPI, show_id: int, root: Path) -> None:
    _scrape(app, show_id)
    with app.state.session_factory() as session:
        library_id = session.get(Item, show_id).library_id  # type: ignore[union-attr]
    with _client(app) as client:
        preview = client.get(f"/api/libraries/{library_id}/arrange-preview").json()
    assert (preview["count"], len(preview["previews"])) == (1, 1)
    assert _run(app, "arrange_library", {"library_id": library_id}).status == "succeeded"
    assert (root / SHOW / "Season 1").is_dir()


def test_tv_colon_keeps_fullwidth() -> None:
    tokens = {"showTitle": "乐队我做东", "title": "第1期：马东 vs: 臧鸿飞"}
    assert (
        render("${showTitle}.${title}", tokens, space=".", colon=" ", fullwidth_colon=False)
        == "乐队我做东.第1期：马东.vs.臧鸿飞"
    )


def test_multi_episode_numbers() -> None:
    assert _episode_numbers(Path("Show.S01E01E02.mkv"), 1) == ("1E2", "01E02")
    assert _episode_numbers(Path("Show.S01E05.mkv"), 5) == ("5", "05")
    assert _episode_numbers(Path("Show.S01E1100.mkv"), 1100) == ("1100", "1100")
    assert _episode_numbers(Path("无编号.mkv"), 3) == ("3", "03")
