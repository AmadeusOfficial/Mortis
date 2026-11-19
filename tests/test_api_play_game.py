"""游戏条目的启动：候选、记住、重选、越界校验，以及视频播放不受游戏播放插件影响。"""

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

FAKE_GAME = """
import os

from pydantic import BaseModel, Field

from mortis.plugin_api import PlayerPlugin


class Settings(BaseModel):
    note: str = Field(default="", title="备注")
    executables: dict[str, str] = {}


class FakeGame(PlayerPlugin):
    id = "fake_game"
    name = "假启动器"
    file_kinds = ("game",)
    Settings = Settings

    def targets(self, host, folder):
        found = []
        for root, _dirs, files in os.walk(folder):
            for name in files:
                lower = name.lower()
                if lower.endswith((".exe", ".bat")) and not lower.startswith("unins"):
                    found.append(os.path.relpath(os.path.join(root, name), folder))
        return sorted(found)

    def play(self, host, path):
        host.launch(path, [], cwd=os.path.dirname(path))


PLUGIN = FakeGame()
"""


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    data = tmp_path / "data"
    folder = data / "plugins" / "fake_game"
    folder.mkdir(parents=True)
    (folder / "plugin.toml").write_text(
        'id = "fake_game"\nname = "假启动器"\nversion = "1.0"\nkind = "player"\n'
        "[mortis]\napi_version = 1\n",
        encoding="utf-8",
    )
    (folder / "__init__.py").write_text(textwrap.dedent(FAKE_GAME), encoding="utf-8")
    return data


@pytest.fixture
def launcher() -> FakeLauncher:
    return FakeLauncher()


@pytest.fixture
def app(data_dir: Path, launcher: FakeLauncher) -> FastAPI:
    return create_app(Settings(data_dir=data_dir), start_worker=False, launcher=launcher)


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app, base_url=LOCAL) as opened:
        yield opened


@pytest.fixture
def store(data_dir: Path) -> ConfigStore:
    return ConfigStore(data_dir)


def _library(client: TestClient, root: Path, data_type: str) -> int:
    root.mkdir(parents=True, exist_ok=True)
    body = {"name": root.name, "data_type_id": data_type, "paths": [str(root)]}
    response = client.post("/api/libraries", json=body)
    assert response.status_code in (200, 201), response.text
    return response.json()["id"]


def _item(app: FastAPI, library_id: int, data_type: str) -> int:
    with app.state.session_factory() as session:
        now = utc_now()
        row = Item(
            library_id=library_id,
            data_type_id=data_type,
            kind="work",
            title="条目",
            created_at=now,
            updated_at=now,
        )
        session.add(row)
        session.commit()
        return row.id


def _file(app: FastAPI, library_id: int, item_id: int, path: Path, kind: str) -> int:
    with app.state.session_factory() as session:
        now = utc_now()
        row = MediaFile(
            library_id=library_id,
            item_id=item_id,
            path=str(path),
            file_kind=kind,
            size=None,
            created_at=now,
            updated_at=now,
        )
        session.add(row)
        session.commit()
        return row.id


def _touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"MZ")
    return path


@pytest.fixture
def game(app: FastAPI, client: TestClient, tmp_path: Path) -> tuple[int, Path, int]:
    """一个游戏条目，文件夹里有 game.exe、sub\\tool.exe 和卸载程序。"""
    root = tmp_path / "游戏"
    library = _library(client, root, "galgame")
    item = _item(app, library, "galgame")
    folder = root / "白色相簿 2"
    _touch(folder / "game.exe")
    _touch(folder / "sub" / "tool.exe")
    _touch(folder / "unins000.exe")
    (folder / "readme.txt").write_text("x", encoding="utf-8")
    file_id = _file(app, library, item, folder, "game")
    return item, folder, file_id


def _remembered(store: ConfigStore) -> dict[str, str]:
    return store.load_config().sources.get("fake_game", {}).get("executables", {})


def test_first_launch_offers_candidates_then_remembers(
    client: TestClient,
    launcher: FakeLauncher,
    store: ConfigStore,
    game: tuple[int, Path, int],
) -> None:
    item, folder, file_id = game
    store.save_plugin("fake_game", values={"note": "别动我"})

    response = client.post(f"/api/items/{item}/play")
    assert response.status_code == 409, response.text
    body = response.json()
    assert body["detail"] == "有 2 个可以启动的程序，选一个再启动"
    assert [c["target"] for c in body["candidates"]] == ["game.exe", str(Path("sub/tool.exe"))]
    assert [c["path"] for c in body["candidates"]] == [
        str(folder / "game.exe"),
        str(folder / "sub" / "tool.exe"),
    ]
    assert [c["name"] for c in body["candidates"]] == ["game.exe", "tool.exe"]
    assert all(c["file_id"] == file_id and c["exists"] for c in body["candidates"])
    assert launcher.launched == []

    picked = client.post(f"/api/items/{item}/play", json={"target": "game.exe"})
    assert picked.status_code == 200, picked.text
    assert picked.json() == {
        "player": "fake_game",
        "player_name": "假启动器",
        "file_id": file_id,
        "path": str(folder / "game.exe"),
    }
    assert launcher.launched == [(str(folder / "game.exe"), [])]
    assert launcher.cwds == [str(folder)]
    assert _remembered(store) == {str(folder): "game.exe"}
    assert store.load_config().sources["fake_game"]["note"] == "别动我"

    again = client.post(f"/api/items/{item}/play")
    assert again.status_code == 200
    assert launcher.launched[-1] == (str(folder / "game.exe"), [])


def test_choose_offers_candidates_again_and_switches(
    client: TestClient,
    launcher: FakeLauncher,
    store: ConfigStore,
    game: tuple[int, Path, int],
) -> None:
    item, folder, _file_id = game
    assert client.post(f"/api/items/{item}/play", json={"target": "game.exe"}).status_code == 200

    response = client.post(f"/api/items/{item}/play", json={"choose": True})
    assert response.status_code == 409
    assert len(response.json()["candidates"]) == 2
    assert len(launcher.launched) == 1

    tool = str(Path("sub/tool.exe"))
    switched = client.post(f"/api/items/{item}/play", json={"target": tool, "choose": True})
    assert switched.status_code == 200
    assert launcher.launched[-1] == (str(folder / "sub" / "tool.exe"), [])
    assert launcher.cwds[-1] == str(folder / "sub")
    assert _remembered(store) == {str(folder): tool}


def test_removed_remembered_exe_offers_candidates(
    client: TestClient,
    launcher: FakeLauncher,
    store: ConfigStore,
    game: tuple[int, Path, int],
) -> None:
    item, folder, _file_id = game
    store.save_plugin("fake_game", values={"executables": {str(folder): "old.exe"}})
    response = client.post(f"/api/items/{item}/play")
    assert response.status_code == 409
    assert launcher.launched == []


def test_remembered_path_outside_folder_is_ignored(
    client: TestClient,
    launcher: FakeLauncher,
    store: ConfigStore,
    game: tuple[int, Path, int],
    tmp_path: Path,
) -> None:
    item, folder, _file_id = game
    _touch(tmp_path / "evil.exe")
    store.save_plugin("fake_game", values={"executables": {str(folder): r"..\..\evil.exe"}})
    response = client.post(f"/api/items/{item}/play")
    assert response.status_code == 409
    assert launcher.launched == []


def test_remembered_non_program_offers_candidates(
    client: TestClient,
    launcher: FakeLauncher,
    store: ConfigStore,
    game: tuple[int, Path, int],
) -> None:
    item, folder, _file_id = game
    store.save_plugin("fake_game", values={"executables": {str(folder): "readme.txt"}})
    response = client.post(f"/api/items/{item}/play")
    assert response.status_code == 409
    assert launcher.launched == []


def test_same_remembered_target_is_not_rewritten(
    client: TestClient,
    launcher: FakeLauncher,
    store: ConfigStore,
    game: tuple[int, Path, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item, folder, _file_id = game
    assert client.post(f"/api/items/{item}/play", json={"target": "game.exe"}).status_code == 200
    writes: list[str] = []
    original = ConfigStore.update_plugin_settings

    def counting(self: ConfigStore, plugin_id: str, change: object) -> object:
        writes.append(plugin_id)
        return original(self, plugin_id, change)  # type: ignore[arg-type]

    monkeypatch.setattr(ConfigStore, "update_plugin_settings", counting)
    assert client.post(f"/api/items/{item}/play").status_code == 200
    assert client.post(f"/api/items/{item}/play", json={"target": "game.exe"}).status_code == 200
    assert writes == []
    assert len(launcher.launched) == 3

    tool = str(Path("sub/tool.exe"))
    assert client.post(f"/api/items/{item}/play", json={"target": tool}).status_code == 200
    assert writes == ["fake_game"]
    assert _remembered(store) == {str(folder): tool}


def test_failed_remember_still_succeeds(
    client: TestClient,
    launcher: FakeLauncher,
    store: ConfigStore,
    game: tuple[int, Path, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item, folder, _file_id = game

    def broken(self: ConfigStore, plugin_id: str, change: object) -> None:
        raise OSError(13, "拒绝访问。")

    monkeypatch.setattr(ConfigStore, "update_plugin_settings", broken)
    response = client.post(f"/api/items/{item}/play", json={"target": "game.exe"})
    assert response.status_code == 200, response.text
    assert launcher.launched == [(str(folder / "game.exe"), [])]
    assert _remembered(store) == {}


def test_target_slashes_match_candidates(
    app: FastAPI,
    client: TestClient,
    launcher: FakeLauncher,
    store: ConfigStore,
    tmp_path: Path,
) -> None:
    root = tmp_path / "脚本"
    library = _library(client, root, "galgame")
    item = _item(app, library, "galgame")
    folder = root / "用脚本启动"
    _touch(folder / "a.exe")
    _touch(folder / "sub" / "start.bat")
    _file(app, library, item, folder, "game")

    response = client.post(f"/api/items/{item}/play", json={"target": "sub/start.bat"})
    assert response.status_code == 200, response.text
    assert launcher.launched == [(str(folder / "sub" / "start.bat"), [])]
    assert _remembered(store) == {str(folder): str(Path("sub/start.bat"))}


def test_bad_executables_entry_keeps_other_settings(
    client: TestClient,
    launcher: FakeLauncher,
    store: ConfigStore,
    game: tuple[int, Path, int],
) -> None:
    item, folder, _file_id = game
    store.save_plugin(
        "fake_game",
        values={"note": "保留", "executables": {str(folder): "game.exe", "坏的": 3}},
    )
    fake = {p["id"]: p for p in client.get("/api/plugins").json()}["fake_game"]
    assert fake["settings"] == {"note": "保留"}
    response = client.post(f"/api/items/{item}/play")
    assert response.status_code == 200, response.text
    assert launcher.launched == [(str(folder / "game.exe"), [])]


def test_playback_setting_rejects_unusable_players(client: TestClient, store: ConfigStore) -> None:
    for player, message in (
        ("fake_game", "播放插件「假启动器」不能播放视频"),
        ("vlc", "没有播放插件「vlc」"),
    ):
        response = client.put("/api/settings/playback", json={"player": player})
        assert response.status_code == 400, response.text
        assert response.json()["detail"] == message
    assert store.load_config().playback.player == ""

    assert client.put("/api/settings/playback", json={"player": "system_player"}).status_code == 200
    assert client.put("/api/plugins/system_player", json={"enabled": False}).status_code == 200
    response = client.put("/api/settings/playback", json={"player": "system_player"})
    assert response.status_code == 400
    assert response.json()["detail"] == "播放插件「系统默认播放器」已停用"
    assert client.put("/api/settings/playback", json={"player": ""}).status_code == 200
    assert store.load_config().playback.player == ""


def test_target_must_stay_inside_folder(
    client: TestClient,
    launcher: FakeLauncher,
    store: ConfigStore,
    game: tuple[int, Path, int],
    tmp_path: Path,
) -> None:
    item, folder, _file_id = game
    outside = _touch(tmp_path / "outside.exe")
    for target in (r"..\..\outside.exe", str(outside), "没有.exe", "readme.txt", ""):
        response = client.post(f"/api/items/{item}/play", json={"target": target})
        assert response.status_code == 400, (target, response.text)
        assert response.json()["detail"] == "选的程序不在游戏文件夹里，或者不能启动"
    assert launcher.launched == []
    assert _remembered(store) == {}

    # 不在候选里、但确实是文件夹里的 .exe，也可以选。
    allowed = client.post(f"/api/items/{item}/play", json={"target": "unins000.exe"})
    assert allowed.status_code == 200
    assert launcher.launched == [(str(folder / "unins000.exe"), [])]


def test_single_candidate_launches_and_remembers(
    app: FastAPI,
    client: TestClient,
    launcher: FakeLauncher,
    store: ConfigStore,
    tmp_path: Path,
) -> None:
    root = tmp_path / "单个"
    library = _library(client, root, "galgame")
    item = _item(app, library, "galgame")
    folder = root / "只有一个"
    _touch(folder / "only.exe")
    _file(app, library, item, folder, "game")

    response = client.post(f"/api/items/{item}/play")
    assert response.status_code == 200, response.text
    assert response.json()["path"] == str(folder / "only.exe")
    assert launcher.launched == [(str(folder / "only.exe"), [])]
    assert _remembered(store) == {str(folder): "only.exe"}


def test_choose_with_single_candidate_asks_first(
    app: FastAPI,
    client: TestClient,
    launcher: FakeLauncher,
    store: ConfigStore,
    tmp_path: Path,
) -> None:
    root = tmp_path / "单个"
    library = _library(client, root, "galgame")
    item = _item(app, library, "galgame")
    folder = root / "只有一个"
    _touch(folder / "only.exe")
    file_id = _file(app, library, item, folder, "game")

    response = client.post(f"/api/items/{item}/play", json={"choose": True})
    assert response.status_code == 409, response.text
    body = response.json()
    assert body["detail"] == "有 1 个可以启动的程序，选一个再启动"
    assert [(c["file_id"], c["target"]) for c in body["candidates"]] == [(file_id, "only.exe")]
    assert launcher.launched == []
    assert _remembered(store) == {}

    picked = client.post(f"/api/items/{item}/play", json={"file_id": file_id, "target": "only.exe"})
    assert picked.status_code == 200, picked.text
    assert launcher.launched == [(str(folder / "only.exe"), [])]
    assert _remembered(store) == {str(folder): "only.exe"}


def test_empty_or_missing_folder(
    app: FastAPI, client: TestClient, launcher: FakeLauncher, tmp_path: Path
) -> None:
    root = tmp_path / "空"
    library = _library(client, root, "galgame")
    empty = _item(app, library, "galgame")
    folder = root / "空文件夹"
    folder.mkdir()
    _file(app, library, empty, folder, "game")
    response = client.post(f"/api/items/{empty}/play")
    assert response.status_code == 404
    assert response.json()["detail"] == "文件夹里没有可以启动的程序"

    gone = _item(app, library, "galgame")
    _file(app, library, gone, root / "不在了", "game")
    response = client.post(f"/api/items/{gone}/play")
    assert response.status_code == 404
    assert response.json()["detail"] == f"游戏文件夹不在了：{root / '不在了'}"
    assert launcher.launched == []


def test_disabled_game_player_is_503_with_reason(
    client: TestClient,
    launcher: FakeLauncher,
    store: ConfigStore,
    game: tuple[int, Path, int],
) -> None:
    item, _folder, _file_id = game
    for plugin_id in ("fake_game", "galgame_launcher"):
        store.save_plugin(plugin_id, disabled=True)
    assert client.post("/api/plugins/reload").status_code == 200
    response = client.post(f"/api/items/{item}/play")
    assert response.status_code == 503
    assert response.json()["detail"] == "Galgame 启动已停用，在插件管理里启用"
    assert launcher.launched == []


def test_disabled_parent_points_to_parent(
    client: TestClient,
    launcher: FakeLauncher,
    store: ConfigStore,
    game: tuple[int, Path, int],
) -> None:
    item, _folder, _file_id = game
    for plugin_id in ("fake_game", "galgame"):
        store.save_plugin(plugin_id, disabled=True)
    assert client.post("/api/plugins/reload").status_code == 200
    response = client.post(f"/api/items/{item}/play")
    assert response.status_code == 503
    assert response.json()["detail"] == "Galgame 插件已停用，在插件管理里启用"
    assert launcher.launched == []


def test_failed_game_player_is_503_with_error(
    client: TestClient,
    launcher: FakeLauncher,
    store: ConfigStore,
    data_dir: Path,
    game: tuple[int, Path, int],
) -> None:
    item, _folder, _file_id = game
    store.save_plugin("fake_game", disabled=True)
    broken = data_dir / "plugins" / "galgame"
    broken.mkdir(parents=True)
    (broken / "plugin.toml").write_text(
        'id = "galgame"\nname = "Galgame"\nversion = "9"\nkind = "data_type"\n'
        "[mortis]\napi_version = 1\n\n"
        '[[players]]\nid = "galgame_launcher"\nname = "Galgame 启动"\n',
        encoding="utf-8",
    )
    (broken / "__init__.py").write_text("PLUGIN = None\n", encoding="utf-8")
    assert client.post("/api/plugins/reload").status_code == 200
    response = client.post(f"/api/items/{item}/play")
    assert response.status_code == 503
    detail = response.json()["detail"]
    assert detail.startswith("Galgame 启动没有加载：父插件 galgame 加载失败"), detail
    assert launcher.launched == []


def test_no_game_player_is_404(
    app: FastAPI,
    client: TestClient,
    launcher: FakeLauncher,
    store: ConfigStore,
    tmp_path: Path,
) -> None:
    root = tmp_path / "电影"
    library = _library(client, root, "movie")
    item = _item(app, library, "movie")
    folder = root / "游戏"
    _touch(folder / "game.exe")
    _file(app, library, item, folder, "game")
    # 停用的 Galgame 启动属于 Galgame 类型，电影条目不提它。
    for plugin_id in ("fake_game", "galgame_launcher"):
        store.save_plugin(plugin_id, disabled=True)
    assert client.post("/api/plugins/reload").status_code == 200
    response = client.post(f"/api/items/{item}/play")
    assert response.status_code == 404
    assert response.json()["detail"] == "没有能启动游戏的插件"
    assert launcher.launched == []


def test_video_playback_ignores_game_players(
    app: FastAPI,
    client: TestClient,
    launcher: FakeLauncher,
    store: ConfigStore,
    tmp_path: Path,
) -> None:
    root = tmp_path / "电影"
    library = _library(client, root, "movie")
    movie = _item(app, library, "movie")
    video = _touch(root / "m.mkv")
    _file(app, library, movie, video, "video")

    # 自动选择时跳过只能启动游戏的 fake_game（它的 id 排在 system_player 前面）。
    response = client.post(f"/api/items/{movie}/play")
    assert response.status_code == 200, response.text
    assert response.json()["player"] == "system_player"
    assert launcher.opened == [str(video)] and launcher.launched == []

    store.save_section("playback", PlaybackConfig(player="fake_game"))
    response = client.post(f"/api/items/{movie}/play")
    assert response.status_code == 503
    assert response.json()["detail"] == "播放插件「假启动器」不能播放视频，在设置的「播放」里换一个"


def test_settings_page_hides_and_keeps_executables(
    client: TestClient, store: ConfigStore, game: tuple[int, Path, int]
) -> None:
    item, folder, _file_id = game
    assert client.post(f"/api/items/{item}/play", json={"target": "game.exe"}).status_code == 200

    plugins = {plugin["id"]: plugin for plugin in client.get("/api/plugins").json()}
    fake = plugins["fake_game"]
    assert "executables" not in fake["settings_schema"]["properties"]
    assert "executables" not in fake["settings"]
    assert fake["settings"] == {"note": ""}

    saved = client.put(
        "/api/plugins/fake_game",
        json={"settings": {"note": "新备注", "executables": {"x": "y.exe"}}},
    )
    assert saved.status_code == 200, saved.text
    values = store.load_config().sources["fake_game"]
    assert values["note"] == "新备注"
    assert values["executables"] == {str(folder): "game.exe"}
