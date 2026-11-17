import textwrap
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mortis.api.app import create_app
from mortis.config.models import PlaybackConfig
from mortis.config.store import ConfigStore
from mortis.db.models import Item, MediaFile, utc_now
from mortis.settings import Settings
from tests.fakes import FakeLauncher

LOCAL = "http://127.0.0.1"

BROKEN = """
from mortis.plugin_api import PlayerError, PlayerPlugin


class Broken(PlayerPlugin):
    id = "broken_player"
    name = "坏播放器"

    def play(self, host, path):
        if path.endswith("boom.mkv"):
            raise RuntimeError("炸了")
        raise PlayerError("解码器没装")


PLUGIN = Broken()
"""


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    folder = tmp_path / "data" / "plugins" / "broken_player"
    folder.mkdir(parents=True)
    (folder / "plugin.toml").write_text(
        'id = "broken_player"\nname = "坏播放器"\nversion = "1.0"\nkind = "player"\n',
        encoding="utf-8",
    )
    (folder / "__init__.py").write_text(textwrap.dedent(BROKEN), encoding="utf-8")
    return tmp_path / "data"


@pytest.fixture
def potplayer_exe(tmp_path: Path, data_dir: Path) -> Path:
    exe = tmp_path / "PotPlayer" / "PotPlayerMini64.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")
    ConfigStore(data_dir).save_plugin("potplayer", values={"executable": str(exe)})
    return exe


@pytest.fixture
def launcher() -> FakeLauncher:
    return FakeLauncher()


@pytest.fixture
def app(data_dir: Path, launcher: FakeLauncher, potplayer_exe: Path) -> FastAPI:
    return create_app(Settings(data_dir=data_dir), start_worker=False, launcher=launcher)


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app, base_url=LOCAL) as opened:
        yield opened


def _library(client: TestClient, tmp_path: Path, name: str, data_type: str) -> int:
    root = tmp_path / name
    root.mkdir(exist_ok=True)
    body = {"name": name, "data_type_id": data_type, "paths": [str(root)]}
    return client.post("/api/libraries", json=body).json()["id"]


def _item(
    app: FastAPI, library_id: int, data_type: str, kind: str, parent: int | None = None
) -> int:
    with app.state.session_factory() as session:
        now = utc_now()
        row = Item(
            library_id=library_id,
            data_type_id=data_type,
            parent_id=parent,
            kind=kind,
            title=f"{kind}-{parent}",
            created_at=now,
            updated_at=now,
        )
        session.add(row)
        session.commit()
        return row.id


def _video(app: FastAPI, library_id: int, item_id: int, path: Path, *, write: bool = True) -> int:
    if write:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")
    with app.state.session_factory() as session:
        now = utc_now()
        row = MediaFile(
            library_id=library_id,
            item_id=item_id,
            path=str(path),
            file_kind="video",
            size=1,
            created_at=now,
            updated_at=now,
        )
        session.add(row)
        session.commit()
        return row.id


@pytest.fixture
def movies(app: FastAPI, client: TestClient, tmp_path: Path) -> tuple[int, int]:
    return _library(client, tmp_path, "电影", "movie"), _library(client, tmp_path, "剧", "tv")


def test_single_video_plays_with_potplayer(
    app: FastAPI,
    client: TestClient,
    movies: tuple[int, int],
    tmp_path: Path,
    launcher: FakeLauncher,
    potplayer_exe: Path,
) -> None:
    library, _ = movies
    movie = _item(app, library, "movie", "work")
    video = tmp_path / "电影" / "黑客帝国 (1999)" / "m.mkv"
    file_id = _video(app, library, movie, video)

    response = client.post(f"/api/items/{movie}/play")
    assert response.status_code == 200, response.text
    assert response.json() == {
        "player": "potplayer",
        "player_name": "PotPlayer",
        "file_id": file_id,
        "path": str(video),
    }
    assert launcher.launched == [(str(potplayer_exe), [str(video)])]
    assert launcher.opened == []


def test_multiple_versions_return_candidates(
    app: FastAPI,
    client: TestClient,
    movies: tuple[int, int],
    tmp_path: Path,
    launcher: FakeLauncher,
) -> None:
    library, _ = movies
    movie = _item(app, library, "movie", "work")
    folder = tmp_path / "电影" / "盗火线 (1995)"
    first = _video(app, library, movie, folder / "Heat.2160p.mkv")
    second = _video(app, library, movie, folder / "Heat.1080p.mkv")
    gone = _video(app, library, movie, folder / "Heat.720p.mkv", write=False)

    response = client.post(f"/api/items/{movie}/play")
    assert response.status_code == 409
    body = response.json()
    assert body["detail"] == "有 3 个视频，选一个再播放"
    assert [c["file_id"] for c in body["candidates"]] == [second, first, gone]
    assert [c["name"] for c in body["candidates"]] == [
        "Heat.1080p.mkv",
        "Heat.2160p.mkv",
        "Heat.720p.mkv",
    ]
    assert [c["exists"] for c in body["candidates"]] == [True, True, False]
    assert launcher.launched == []

    picked = client.post(f"/api/items/{movie}/play", json={"file_id": first})
    assert picked.status_code == 200
    assert launcher.launched[-1][1] == [str(folder / "Heat.2160p.mkv")]

    missing = client.post(f"/api/items/{movie}/play", json={"file_id": gone})
    assert missing.status_code == 404
    assert missing.json()["detail"].startswith("文件不在了")

    other = client.post(f"/api/items/{movie}/play", json={"file_id": 99999})
    assert other.status_code == 404
    assert other.json()["detail"] == "这个条目没有这个视频文件"


def test_missing_file_is_404(
    app: FastAPI,
    client: TestClient,
    movies: tuple[int, int],
    tmp_path: Path,
    launcher: FakeLauncher,
) -> None:
    library, _ = movies
    movie = _item(app, library, "movie", "work")
    _video(app, library, movie, tmp_path / "电影" / "没了.mkv", write=False)
    response = client.post(f"/api/items/{movie}/play")
    assert response.status_code == 404
    assert response.json()["detail"] == f"文件不在了：{tmp_path / '电影' / '没了.mkv'}"
    assert launcher.launched == []

    empty = _item(app, library, "movie", "work")
    response = client.post(f"/api/items/{empty}/play")
    assert response.status_code == 404
    assert response.json()["detail"] == "这个条目没有视频文件"
    assert client.post("/api/items/99999/play").status_code == 404


def test_show_season_and_collection_cannot_play(
    app: FastAPI,
    client: TestClient,
    movies: tuple[int, int],
    tmp_path: Path,
    launcher: FakeLauncher,
) -> None:
    library, tv = movies
    show = _item(app, tv, "tv", "work")
    season = _item(app, tv, "tv", "season", show)
    episode = _item(app, tv, "tv", "episode", season)
    _video(app, tv, show, tmp_path / "剧" / "挂在剧上.mkv")
    video = tmp_path / "剧" / "S01E01.mkv"
    _video(app, tv, episode, video)
    collection = _item(app, library, "movie", "collection")

    for item_id, message in (
        (show, "剧不能直接播放，选一集再播放"),
        (season, "季不能直接播放，选一集再播放"),
        (collection, "系列电影不能直接播放，打开其中一部再播放"),
    ):
        response = client.post(f"/api/items/{item_id}/play")
        assert response.status_code == 400
        assert response.json()["detail"] == message
    assert launcher.launched == []

    played = client.post(f"/api/items/{episode}/play")
    assert played.status_code == 200
    assert launcher.launched == [(launcher.launched[0][0], [str(video)])]


def test_system_player_and_plugin_errors(
    app: FastAPI,
    client: TestClient,
    movies: tuple[int, int],
    tmp_path: Path,
    launcher: FakeLauncher,
    data_dir: Path,
) -> None:
    library, _ = movies
    movie = _item(app, library, "movie", "work")
    video = tmp_path / "电影" / "m.mkv"
    _video(app, library, movie, video)
    store = ConfigStore(data_dir)

    saved = client.put("/api/settings/playback", json={"player": "system_player"})
    assert saved.status_code == 200 and saved.json()["playback"] == {"player": "system_player"}
    response = client.post(f"/api/items/{movie}/play")
    assert response.json()["player_name"] == "系统默认播放器"
    assert launcher.opened == [str(video)] and launcher.launched == []

    store.save_section("playback", PlaybackConfig(player="broken_player"))
    response = client.post(f"/api/items/{movie}/play")
    assert response.status_code == 502
    assert response.json()["detail"] == "坏播放器 没能打开：解码器没装"

    boom = _item(app, library, "movie", "work")
    _video(app, library, boom, tmp_path / "电影" / "boom.mkv")
    response = client.post(f"/api/items/{boom}/play")
    assert response.status_code == 502
    assert response.json()["detail"] == "坏播放器 出错：RuntimeError: 炸了"

    store.save_section("playback", PlaybackConfig(player="potplayer"))
    launcher.fail = PermissionError(13, "拒绝访问。")
    response = client.post(f"/api/items/{movie}/play")
    assert response.status_code == 502
    assert (
        response.json()["detail"] == "PotPlayer 没能打开：启动 PotPlayerMini64.exe 失败：拒绝访问。"
    )


def test_unavailable_or_unknown_player(
    app: FastAPI,
    client: TestClient,
    movies: tuple[int, int],
    tmp_path: Path,
    launcher: FakeLauncher,
    data_dir: Path,
) -> None:
    library, _ = movies
    movie = _item(app, library, "movie", "work")
    _video(app, library, movie, tmp_path / "电影" / "m.mkv")
    store = ConfigStore(data_dir)

    store.save_plugin("potplayer", values={"executable": str(tmp_path / "没有.exe")})
    response = client.post(f"/api/items/{movie}/play")
    assert response.status_code == 503
    assert response.json()["detail"] == f"PotPlayer 不可用：填的路径找不到：{tmp_path / '没有.exe'}"

    store.save_section("playback", PlaybackConfig(player="vlc"))
    response = client.post(f"/api/items/{movie}/play")
    assert response.status_code == 503
    assert response.json()["detail"] == "没有播放插件「vlc」，在设置的「播放」里换一个"

    assert client.put("/api/plugins/system_player", json={"enabled": False}).status_code == 200
    store.save_section("playback", PlaybackConfig(player="system_player"))
    response = client.post(f"/api/items/{movie}/play")
    assert response.json()["detail"] == "播放插件「系统默认播放器」已停用"

    # 没选时按 id 顺序用第一个可用的，这里是坏播放器。
    store.save_section("playback", PlaybackConfig(player=""))
    response = client.post(f"/api/items/{movie}/play")
    assert response.json()["detail"] == "坏播放器 没能打开：解码器没装"
    assert launcher.launched == [] and launcher.opened == []


def test_plugins_list_shows_player_status(
    client: TestClient, potplayer_exe: Path, tmp_path: Path
) -> None:
    plugins = {plugin["id"]: plugin for plugin in client.get("/api/plugins").json()}
    pot = plugins["potplayer"]
    assert pot["kind"] == "player"
    assert pot["available"] is True and pot["notice"] is None
    assert pot["detected_path"] == str(potplayer_exe)
    assert pot["settings"] == {"executable": str(potplayer_exe)}
    assert pot["settings_schema"]["properties"]["executable"]["title"] == "播放器路径"
    assert plugins["system_player"]["available"] is True
    assert plugins["vndb"]["available"] is None

    saved = client.put(
        "/api/plugins/potplayer", json={"settings": {"executable": str(tmp_path / "x.exe")}}
    ).json()
    assert saved["available"] is False
    assert saved["notice"] == f"填的路径找不到：{tmp_path / 'x.exe'}"
    assert saved["detected_path"] is None
    rejected = client.put("/api/plugins/potplayer", json={"secrets": {"a": "b"}})
    assert rejected.status_code == 422
    assert rejected.json()["detail"] == "这个插件没有密钥"

    assert client.get("/api/settings").json()["playback"] == {"player": "potplayer"}
    bad = client.put("/api/settings/playback", json={"player": "Bad Id"})
    assert bad.status_code == 422
