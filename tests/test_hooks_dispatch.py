"""事件发布、钩子任务、限额、熔断、防循环、标签权限。钩子都写成真实的插件包放进插件目录。"""

import json
import textwrap
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx2 as httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from mortis.api.app import create_app
from mortis.config.models import Secrets
from mortis.db.models import (
    Item,
    ItemExternalId,
    ItemTagOrigin,
    Library,
    LibraryPath,
    MediaFile,
    Tag,
    Task,
    utc_now,
)
from mortis.hooks import dispatch
from mortis.hooks.dispatch import HOOK_TASK, enqueue_events, hook_payload
from mortis.items.identity import MovieRef, find_or_create_movie
from mortis.items.tags import add_tags, set_item_tags
from mortis.libraries import create_library
from mortis.plugin_api import ScrapeFinished
from mortis.settings import Settings
from mortis.tasks import repo
from mortis.tasks.base import TaskContext, TaskResult
from mortis.tasks.runner import TaskRunner
from tests.fakes import FakeLauncher, FakeTmdb, hit, load_fixture
from tests.test_scrape_item import DATA_TYPE, SOURCE, FakeSites
from tests.test_scrape_item import _write as write_plugin_dir

LOCAL = "http://127.0.0.1"
PUBLIC = "93.184.216.34"
BUNDLED = Path(__file__).resolve().parents[1] / "plugins"

PREAMBLE = """
import json
from mortis.plugin_api import HookPlugin, Plugin


def record(host, event, label):
    with (host.data_dir / "events.jsonl").open("a", encoding="utf-8") as out:
        out.write(json.dumps({"hook": label, "event": event.model_dump(mode="json")}) + "\\n")


def dump(host, name, value):
    (host.data_dir / name).write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
"""


@dataclass
class H:
    id: str
    event: str
    body: str = ""
    data_types: tuple[str, ...] = ()
    writes: tuple[str, ...] = ()
    extra: dict[str, Any] = field(default_factory=dict)


def write_hooks(root: Path, plugin_id: str, hooks: list[H]) -> None:
    """一个插件包，带这些钩子；没写 body 的钩子把收到的事件记到自己数据目录的 events.jsonl。"""
    folder = root / plugin_id.replace(".", "_")
    folder.mkdir(parents=True, exist_ok=True)
    manifest = (
        f'id = "{plugin_id}"\nname = "{plugin_id}"\nversion = "1"\n[mortis]\napi_version = 1\n'
    )
    classes: list[str] = []
    objects: list[str] = []
    for index, hook in enumerate(hooks):
        manifest += (
            f'[[hooks]]\nid = "{hook.id}"\nevent = "{hook.event}"\n'
            f"data_types = {json.dumps(list(hook.data_types))}\n"
            f"writes = {json.dumps(list(hook.writes))}\n"
        )
        body = textwrap.dedent(hook.body or f'record(host, event, "{hook.id}")').strip("\n")
        classes.append(
            f'class H{index}(HookPlugin):\n    id = "{hook.id}"\n    name = "{hook.id}"\n\n'
            f"    def on_event(self, host, event):\n{textwrap.indent(body, ' ' * 8)}\n"
        )
        objects.append(f"H{index}()")
    code = (
        PREAMBLE
        + "\n\n"
        + "\n\n".join(classes)
        + f"\nPLUGIN = Plugin(hooks=({', '.join(objects)},))\n"
    )
    (folder / "plugin.toml").write_text(manifest, encoding="utf-8")
    (folder / "__init__.py").write_text(code, encoding="utf-8")


def recorded(app: FastAPI, plugin_id: str) -> list[dict[str, Any]]:
    path = app.state.settings.data_dir / "plugin_data" / plugin_id / "events.jsonl"
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def events_of(app: FastAPI, plugin_id: str, hook_id: str) -> list[dict[str, Any]]:
    return [row["event"] for row in recorded(app, plugin_id) if row["hook"] == hook_id]


def dumped(app: FastAPI, plugin_id: str, name: str) -> Any:
    path = app.state.settings.data_dir / "plugin_data" / plugin_id / name
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def run(app: FastAPI) -> None:
    app.state.task_runner.run_until_idle()


def tasks(app: FastAPI, task_type: str | None = None) -> list[Task]:
    with app.state.session_factory() as session:
        found = list(session.scalars(select(Task).order_by(Task.id)))
        session.expunge_all()
    return [task for task in found if task_type is None or task.task_type == task_type]


def enqueue_scrape(app: FastAPI, item_id: int, force: bool = False) -> int:
    with app.state.session_factory() as session:
        task = repo.enqueue(session, "scrape", {"item_id": item_id, "force": force})
        session.commit()
        return task.id


# ---- 电影应用 ----


def movie_fake() -> FakeTmdb:
    fake = FakeTmdb()
    fake.add("/3/movie/603", load_fixture("movie_603.json"))
    fake.add("/3/collection/2344", load_fixture("collection_2344.json"))
    fake.add("/3/collection/2344/images", load_fixture("collection_2344_images.json"))
    fake.add_search("The Matrix", [hit(603, "黑客帝国", "1999-03-30", "The Matrix")])
    fake.add_search("Heat", [hit(949, "盗火线", "1995-12-15", "Heat")])
    return fake


def movie_app(
    tmp_path: Path,
    plugins: dict[str, list[H]],
    *,
    handler: Any = None,
    launcher: FakeLauncher | None = None,
    bundled: bool = False,
) -> FastAPI:
    root = tmp_path / "data" / "plugins"
    for plugin_id, hooks in plugins.items():
        write_hooks(root, plugin_id, hooks)
    fake = movie_fake()
    transport = httpx.MockTransport(handler(fake) if handler else fake)
    app = create_app(
        Settings(data_dir=tmp_path / "data", plugins_dir=BUNDLED if bundled else None),
        http_transport=transport,
        start_worker=False,
        launcher=launcher,
    )
    app.state.config_store.save_secrets(Secrets(sources={"tmdb": {"api_key": "k"}}))
    return app


def movie_item(app: FastAPI, tmp_path: Path, *, video: bool = True) -> int:
    root = tmp_path / "lib"
    (root / "matrix").mkdir(parents=True, exist_ok=True)
    with app.state.session_factory() as session:
        now = utc_now()
        library = Library(data_type_id="movie", name="电影", created_at=now, updated_at=now)
        session.add(library)
        session.flush()
        session.add(LibraryPath(library_id=library.id, path=str(root)))
        item, _ = find_or_create_movie(session, library, MovieRef(603, "The Matrix"))
        if video:
            path = root / "matrix" / "matrix.mkv"
            path.write_bytes(b"video")
            session.add(
                MediaFile(
                    library_id=library.id,
                    item_id=item.id,
                    path=str(path),
                    file_kind="video",
                    created_at=now,
                    updated_at=now,
                )
            )
        session.commit()
        return item.id


def hook_tasks_of(app: FastAPI, parent_id: int) -> list[Task]:
    return [t for t in tasks(app, HOOK_TASK) if t.parent_task_id == parent_id]


# ---- 事件发布 ----


def test_movie_scrape_and_arrange_publish_events(tmp_path: Path) -> None:
    app = movie_app(
        tmp_path,
        {
            "t.rec": [
                H("t.started", "scrape.started"),
                H("t.finished", "scrape.finished"),
                H("t.failed", "scrape.failed"),
                H("t.arranged", "arrange.finished"),
            ]
        },
    )
    item_id = movie_item(app, tmp_path)
    scrape_id = enqueue_scrape(app, item_id)
    run(app)
    for hook_id, kind in (("t.started", "scrape.started"), ("t.finished", "scrape.finished")):
        (event,) = events_of(app, "t.rec", hook_id)
        assert event["type"] == kind
        assert (event["item_id"], event["data_type"], event["task_id"]) == (
            item_id,
            "movie",
            scrape_id,
        )
        assert event["force"] is False and event["library_id"] > 0
    assert events_of(app, "t.rec", "t.failed") == []
    (arranged,) = events_of(app, "t.rec", "t.arranged")
    assert (arranged["item_id"], arranged["data_type"]) == (item_id, "movie")
    assert arranged["written"] > 0 and arranged["task_id"] > 0
    # 钩子任务挂在触发它的任务下面，库提交之后才跑。
    assert {t.status for t in hook_tasks_of(app, scrape_id)} == {"succeeded"}
    assert len(hook_tasks_of(app, scrape_id)) == 2


def test_hooks_only_get_their_data_types(tmp_path: Path) -> None:
    app = movie_app(
        tmp_path,
        {
            "t.rec": [
                H("t.any", "scrape.finished"),
                H("t.movie", "scrape.finished", data_types=("movie",)),
                H("t.tv", "scrape.finished", data_types=("tv",)),
            ]
        },
    )
    scrape_id = enqueue_scrape(app, movie_item(app, tmp_path))
    run(app)
    assert len(events_of(app, "t.rec", "t.any")) == 1
    assert len(events_of(app, "t.rec", "t.movie")) == 1
    assert events_of(app, "t.rec", "t.tv") == []
    assert len(hook_tasks_of(app, scrape_id)) == 2  # 没订阅这个类型的钩子根本不排任务


def test_series_path_publishes_through_handle_scrape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("mortis.scrape.service.scrape_show", lambda *a, **k: {"shows": 1})
    app = movie_app(
        tmp_path,
        {
            "t.rec": [
                H("t.finished", "scrape.finished", data_types=("tv",)),
                H("t.movie", "scrape.finished", data_types=("movie",)),
            ]
        },
    )
    with app.state.session_factory() as session:
        library = create_library(session, name="剧", data_type_id="tv", paths=[str(tmp_path)])
        now = utc_now()
        show = Item(
            library_id=library.id,
            data_type_id="tv",
            kind="work",
            title="剧",
            created_at=now,
            updated_at=now,
        )
        session.add(show)
        session.commit()
        show_id = show.id
    enqueue_scrape(app, show_id)
    run(app)
    (event,) = events_of(app, "t.rec", "t.finished")
    assert (event["item_id"], event["data_type"]) == (show_id, "tv")
    assert events_of(app, "t.rec", "t.movie") == []


def test_scrape_failure_publishes_failed_and_task_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mortis.tasks.base import TaskError

    def boom(*args: object, **kwargs: object) -> dict[str, Any]:
        raise TaskError("TMDB 炸了")

    monkeypatch.setattr("mortis.scrape.service.scrape_movie", boom)
    app = movie_app(
        tmp_path,
        {
            "t.rec": [
                H("t.started", "scrape.started"),
                H("t.finished", "scrape.finished"),
                H("t.failed", "scrape.failed"),
                H("t.task", "task.failed"),
            ]
        },
    )
    item_id = movie_item(app, tmp_path)
    scrape_id = enqueue_scrape(app, item_id)
    run(app)
    (scrape,) = tasks(app, "scrape")
    assert scrape.status == "failed" and scrape.error == "TMDB 炸了"
    assert len(events_of(app, "t.rec", "t.started")) == 1
    assert events_of(app, "t.rec", "t.finished") == []
    (failed,) = events_of(app, "t.rec", "t.failed")
    assert (failed["item_id"], failed["error"], failed["task_id"]) == (
        item_id,
        "TMDB 炸了",
        scrape_id,
    )
    (task_failed,) = events_of(app, "t.rec", "t.task")
    assert (task_failed["task_type"], task_failed["item_id"], task_failed["data_type"]) == (
        "scrape",
        item_id,
        "movie",
    )
    assert task_failed["error"] == "TMDB 炸了"


@pytest.fixture
def sites() -> FakeSites:
    sites = FakeSites()
    sites.hits["alpha"] = [{"external_id": "a1", "title": "星之梦", "year": 2010}]
    sites.items["alpha/item/a1"] = {"values": {"title": "星之梦"}}
    return sites


def doujin_app(tmp_path: Path, sites: FakeSites, hooks: list[H]) -> FastAPI:
    plugins = tmp_path / "plugins"
    write_plugin_dir(plugins, "doujin", "data_type", DATA_TYPE)
    write_plugin_dir(plugins, "alpha", "source", SOURCE.format(id="alpha", name="Alpha"))
    write_plugin_dir(plugins, "beta", "source", SOURCE.format(id="beta", name="Beta"))
    write_hooks(plugins, "t.rec", hooks)
    return create_app(
        Settings(data_dir=tmp_path / "data", plugins_dir=plugins),
        http_transport=httpx.MockTransport(sites.handler),
        start_worker=False,
    )


def test_data_type_plugin_path_publishes_too(tmp_path: Path, sites: FakeSites) -> None:
    """数据类型插件的刮削在 handle_scrape 里提前返回，也要走到发布处。"""
    app = doujin_app(
        tmp_path,
        sites,
        [
            H("t.started", "scrape.started", data_types=("doujin",)),
            H("t.finished", "scrape.finished", data_types=("doujin",)),
            H("t.movie", "scrape.finished", data_types=("movie",)),
        ],
    )
    with app.state.session_factory() as session:
        now = utc_now()
        library = Library(data_type_id="doujin", name="同人", created_at=now, updated_at=now)
        session.add(library)
        session.flush()
        item = Item(
            library_id=library.id,
            data_type_id="doujin",
            kind="work",
            title="星之梦",
            created_at=now,
            updated_at=now,
        )
        session.add(item)
        session.flush()
        session.add(ItemExternalId(item_id=item.id, provider="alpha", external_id="a1"))
        session.commit()
        item_id = item.id
    scrape_id = enqueue_scrape(app, item_id)
    run(app)
    (scrape,) = tasks(app, "scrape")
    assert scrape.status == "succeeded", scrape.error
    (event,) = events_of(app, "t.rec", "t.finished")
    assert (event["item_id"], event["data_type"], event["task_id"]) == (
        item_id,
        "doujin",
        scrape_id,
    )
    assert len(events_of(app, "t.rec", "t.started")) == 1
    assert events_of(app, "t.rec", "t.movie") == []


def test_organize_publishes_item_created_and_scan_finished(tmp_path: Path) -> None:
    app = movie_app(
        tmp_path,
        {
            "t.rec": [
                H("t.created", "item.created"),
                H("t.scan", "scan.finished"),
                H("t.other", "scan.finished", data_types=("tv",)),
            ]
        },
    )
    root = tmp_path / "电影"
    for rel in (
        "The Matrix (1999)/The.Matrix.1999.mkv",
        "Heat (1995)/Heat.cd1.mkv",
        "x (2031)/x.mkv",
    ):
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"12345")
    with app.state.session_factory() as session:
        library = create_library(session, name="电影", data_type_id="movie", paths=[str(root)])
        library_id = library.id
        task = repo.enqueue(session, "organize", {"library_id": library_id, "scrape": False})
        session.commit()
        organize_id = task.id
    run(app)
    with app.state.session_factory() as session:
        ids = sorted(session.scalars(select(Item.id)))
    created = events_of(app, "t.rec", "t.created")
    assert sorted(e["item_id"] for e in created) == ids and len(ids) == 2
    assert {e["data_type"] for e in created} == {"movie"}
    (scan,) = events_of(app, "t.rec", "t.scan")
    assert (scan["library_id"], scan["found"], scan["matched"], scan["unmatched"]) == (
        library_id,
        3,
        2,
        1,
    )
    assert events_of(app, "t.rec", "t.other") == []
    assert {t.parent_task_id for t in tasks(app, HOOK_TASK)} == {organize_id}
    # 再扫一次没有新条目，不会重复发 item.created。
    with app.state.session_factory() as session:
        repo.enqueue(session, "organize", {"library_id": library_id, "scrape": False})
        session.commit()
    run(app)
    assert len(events_of(app, "t.rec", "t.created")) == 2
    assert len(events_of(app, "t.rec", "t.scan")) == 2


def test_plugin_type_organize_publishes(tmp_path: Path, sites: FakeSites) -> None:
    app = doujin_app(
        tmp_path,
        sites,
        [H("t.created", "item.created"), H("t.scan", "scan.finished")],
    )
    sites.hits["beta"] = [{"external_id": "b9", "title": "星之梦"}]
    games = tmp_path / "games"
    (games / "星之梦").mkdir(parents=True)
    with app.state.session_factory() as session:
        library = create_library(session, name="同人", data_type_id="doujin", paths=[str(games)])
        library.is_auto_scrape = 0
        repo.enqueue(session, "organize", {"library_id": library.id})
        session.commit()
    run(app)
    (created,) = events_of(app, "t.rec", "t.created")
    assert created["data_type"] == "doujin" and created["item_id"] > 0
    (scan,) = events_of(app, "t.rec", "t.scan")
    assert (scan["data_type"], scan["found"], scan["matched"]) == ("doujin", 1, 1)


def test_play_publishes_started(tmp_path: Path) -> None:
    launcher = FakeLauncher()
    app = movie_app(
        tmp_path,
        {"t.rec": [H("t.play", "play.started")]},
        launcher=launcher,
        bundled=True,
    )
    item_id = movie_item(app, tmp_path)
    with TestClient(app, base_url=LOCAL) as client:
        response = client.post(f"/api/items/{item_id}/play")
        assert response.status_code == 200, response.text
        file_id = response.json()["file_id"]
        run(app)
    assert launcher.opened
    (event,) = events_of(app, "t.rec", "t.play")
    assert (event["item_id"], event["file_id"], event["data_type"]) == (item_id, file_id, "movie")
    assert event["player"] == "system_player"


def test_play_with_choices_does_not_publish(tmp_path: Path) -> None:
    app = movie_app(
        tmp_path, {"t.rec": [H("t.play", "play.started")]}, launcher=FakeLauncher(), bundled=True
    )
    item_id = movie_item(app, tmp_path)
    with app.state.session_factory() as session:
        item = session.get(Item, item_id)
        assert item is not None
        now = utc_now()
        extra = tmp_path / "lib" / "matrix" / "b.mkv"
        extra.write_bytes(b"x")
        session.add(
            MediaFile(
                library_id=item.library_id,
                item_id=item_id,
                path=str(extra),
                file_kind="video",
                created_at=now,
                updated_at=now,
            )
        )
        session.commit()
    with TestClient(app, base_url=LOCAL) as client:
        assert client.post(f"/api/items/{item_id}/play").status_code == 409
        run(app)
    assert events_of(app, "t.rec", "t.play") == []


# ---- 故障隔离、熔断、防循环 ----

RAISE = 'raise RuntimeError("炸了")'


def test_failing_hook_is_isolated_and_does_not_trigger_task_failed(tmp_path: Path) -> None:
    app = movie_app(
        tmp_path,
        {
            "t.bad": [H("t.boom", "scrape.finished", RAISE)],
            "t.good": [H("t.fine", "scrape.finished"), H("t.watch", "task.failed")],
        },
    )
    scrape_id = enqueue_scrape(app, movie_item(app, tmp_path))
    run(app)
    (scrape,) = tasks(app, "scrape")
    assert scrape.status == "succeeded"
    assert len(events_of(app, "t.good", "t.fine")) == 1  # 别的钩子照常收到
    by_hook = {json.loads(t.payload or "{}")["hook_id"]: t for t in hook_tasks_of(app, scrape_id)}
    assert by_hook["t.boom"].status == "failed"
    assert "RuntimeError: 炸了" in (by_hook["t.boom"].error or "")
    assert by_hook["t.fine"].status == "succeeded"
    # 钩子失败不发布 task.failed，所以订阅它的钩子什么也没收到、也没排钩子任务。
    assert events_of(app, "t.good", "t.watch") == []
    assert len(tasks(app, HOOK_TASK)) == 2
    status = app.state.services.plugins.hook_status("t.boom")
    assert (status.failures, status.last_error, status.disabled) == (
        1,
        "RuntimeError: 炸了",
        False,
    )


def test_fuse_disables_hook_after_three_failures_and_persists(tmp_path: Path) -> None:
    app = movie_app(tmp_path, {"t.bad": [H("t.boom", "scrape.finished", RAISE)]})
    item_id = movie_item(app, tmp_path)
    registry = app.state.services.plugins
    for _ in range(3):
        enqueue_scrape(app, item_id, force=True)
        run(app)
    assert [t.status for t in tasks(app, HOOK_TASK)] == ["failed"] * 3
    status = registry.hook_status("t.boom")
    assert status.disabled and "连续失败 3 次" in (status.reason or "")
    config = app.state.config_store.load_config()
    assert "t.boom" in config.plugins.disabled  # 写进了 [plugins] disabled
    assert registry.hooks() == []
    # 之后的事件不再给它排任务，刮削照常成功。
    enqueue_scrape(app, item_id, force=True)
    run(app)
    assert len(tasks(app, HOOK_TASK)) == 3
    assert all(t.status == "succeeded" for t in tasks(app, "scrape"))
    # 重启后按设置文件仍是停用；从名单里去掉再重新加载就是重新启用。
    registry.load(config.plugins.disabled)
    assert registry.hook_status("t.boom").disabled
    app.state.config_store.save_plugin("t.boom", disabled=False)
    registry.load(app.state.config_store.load_config().plugins.disabled)
    assert not registry.hook_status("t.boom").disabled
    enqueue_scrape(app, item_id, force=True)
    run(app)
    assert len(tasks(app, HOOK_TASK)) == 4


def test_queued_hook_task_is_skipped_once_hook_is_gone(tmp_path: Path) -> None:
    app = movie_app(tmp_path, {"t.rec": [H("t.finished", "scrape.finished")]})
    item_id = movie_item(app, tmp_path, video=False)
    with app.state.session_factory() as session:
        event = ScrapeFinished(item_id=item_id, data_type="movie")
        enqueue_events(session, app.state.services, event)
    app.state.services.plugins.disable_hook("t.finished")
    run(app)
    (task,) = tasks(app, HOOK_TASK)
    assert task.status == "succeeded" and json.loads(task.result or "{}") == {"skipped": True}
    assert recorded(app, "t.rec") == []


def test_hook_tasks_never_publish_events(tmp_path: Path) -> None:
    """钩子加标签会排一个静默的整理任务，它不再发布 arrange.finished，也就不会循环。"""
    app = movie_app(
        tmp_path,
        {
            "t.tags": [
                H(
                    "t.tag",
                    "scrape.finished",
                    'host.add_tags(event.item_id, "keyword", ["插件标签"])',
                    writes=("tags:keyword",),
                ),
                H(
                    "t.again",
                    "arrange.finished",
                    'record(host, event, "t.again")\n'
                    'host.add_tags(event.item_id, "keyword", ["又一个" + str(event.task_id)])',
                    writes=("tags:keyword",),
                ),
            ]
        },
    )
    enqueue_scrape(app, movie_item(app, tmp_path))
    run(app)
    arranges = tasks(app, "arrange")
    assert len(arranges) == 3  # 刮削后的整理，以及两个钩子各自引起的静默重写
    assert [json.loads(t.payload or "{}").get("silent") for t in arranges] == [None, True, True]
    assert len(events_of(app, "t.tags", "t.again")) == 1
    assert all(t.status == "succeeded" for t in tasks(app))


# ---- 额度和联网 ----


def api_handler(seen: list[str]) -> Any:
    def make(fake: FakeTmdb) -> Any:
        def handle(request: httpx.Request) -> httpx.Response:
            if request.url.host == "api.example.org":
                seen.append(str(request.url))
                if request.url.path == "/redirect":
                    return httpx.Response(302, headers={"location": "http://169.254.169.254/x"})
                return httpx.Response(200, json={"echo": request.url.path})
            if request.url.host in ("127.0.0.1", "169.254.169.254"):
                seen.append(f"LEAK {request.url}")
                return httpx.Response(200, json={})
            return fake(request)

        return handle

    return make


@pytest.fixture
def public_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("mortis.hooks.netguard.resolve", lambda host: [PUBLIC])


def test_hook_can_use_host_client(tmp_path: Path, public_dns: None) -> None:
    seen: list[str] = []
    app = movie_app(
        tmp_path,
        {
            "t.net": [
                H(
                    "t.get",
                    "scrape.finished",
                    'url = "https://api.example.org/a"\n'
                    'dump(host, "got.json", host.get_json(url, params={"q": 1}))',
                )
            ]
        },
        handler=api_handler(seen),
    )
    enqueue_scrape(app, movie_item(app, tmp_path))
    run(app)
    assert dumped(app, "t.net", "got.json") == {"echo": "/a"}
    assert seen == ["https://api.example.org/a?q=1"]
    assert (app.state.settings.data_dir / "plugin_data" / "t.net").is_dir()


@pytest.mark.parametrize(
    "call",
    [
        'host.get_json("http://127.0.0.1:9/x")',
        'host.post_json("http://169.254.169.254/latest", json={})',
        'host.get_json("https://api.example.org/redirect")',
    ],
)
def test_hook_cannot_reach_local_or_private_addresses(
    tmp_path: Path, public_dns: None, call: str
) -> None:
    seen: list[str] = []
    app = movie_app(
        tmp_path,
        {"t.net": [H("t.get", "scrape.finished", call)]},
        handler=api_handler(seen),
    )
    scrape_id = enqueue_scrape(app, movie_item(app, tmp_path))
    run(app)
    (task,) = hook_tasks_of(app, scrape_id)
    assert task.status == "failed" and "HookLimitError" in (task.error or "")
    assert not any(row.startswith("LEAK") for row in seen)


def test_hook_http_budget_per_event(tmp_path: Path, public_dns: None) -> None:
    seen: list[str] = []
    app = movie_app(
        tmp_path,
        {
            "t.net": [
                H(
                    "t.get",
                    "scrape.finished",
                    'for index in range(8):\n    host.get_json(f"https://api.example.org/{index}")',
                )
            ]
        },
        handler=api_handler(seen),
    )
    enqueue_scrape(app, movie_item(app, tmp_path))
    run(app)
    (task,) = tasks(app, HOOK_TASK)
    assert task.status == "failed" and "最多联网 5 次" in (task.error or "")
    assert len(seen) == 5


def test_hook_queue_is_capped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dispatch, "QUEUE_LIMIT", 2)
    app = movie_app(tmp_path, {"t.rec": [H("t.finished", "scrape.finished")]})
    events = [ScrapeFinished(item_id=i, data_type="movie") for i in range(1, 6)]
    with app.state.session_factory() as session:
        assert enqueue_events(session, app.state.services, *events) == 2
        assert enqueue_events(session, app.state.services, *events) == 0
    assert len(tasks(app, HOOK_TASK)) == 2


def test_events_without_subscribers_queue_nothing(tmp_path: Path) -> None:
    app = movie_app(tmp_path, {})
    enqueue_scrape(app, movie_item(app, tmp_path))
    run(app)
    assert tasks(app, HOOK_TASK) == []
    assert tasks(app, "scrape")[0].status == "succeeded"


def test_hook_tasks_run_after_regular_tasks(tmp_path: Path) -> None:
    app = movie_app(tmp_path, {"t.rec": [H("t.finished", "scrape.finished")]})
    with app.state.session_factory() as session:
        repo.enqueue(
            session,
            HOOK_TASK,
            hook_payload("t.rec", "t.finished", ScrapeFinished(item_id=1, data_type="movie")),
            priority=dispatch.HOOK_PRIORITY,
        )
        later = repo.enqueue(session, "arrange_library", {"library_id": 99})
        session.commit()
        later_id = later.id
    app.state.task_runner.run_once()
    assert next(t for t in tasks(app) if t.id == later_id).status != "queued"
    assert tasks(app, HOOK_TASK)[0].status == "queued"


# ---- 并发限额 ----


def _enqueue_hook(app: FastAPI, plugin_id: str, hook_id: str) -> None:
    with app.state.session_factory() as session:
        repo.enqueue(
            session,
            HOOK_TASK,
            hook_payload(plugin_id, hook_id, ScrapeFinished(item_id=1, data_type="movie")),
            priority=dispatch.HOOK_PRIORITY,
        )
        session.commit()


def test_runner_skips_busy_plugin_and_full_hook_quota(tmp_path: Path) -> None:
    app = movie_app(tmp_path, {})
    runner: TaskRunner = app.state.task_runner
    seen: list[str] = []

    def handler(payload: dict[str, Any], ctx: TaskContext) -> TaskResult:
        seen.append(payload.get("plugin_id") or payload.get("kind", ""))
        return TaskResult()

    runner.register(HOOK_TASK, handler)
    runner.register("other", handler)
    for plugin in ("a", "a", "b"):
        _enqueue_hook(app, plugin, "h")
    with app.state.session_factory() as session:
        repo.enqueue(session, "other", {"kind": "other"}, priority=-9)
        session.commit()

    runner._hook_plugins[900] = "a"  # a 的钩子正在跑：只能领 b 的
    assert runner.run_once() and seen == ["b"]
    assert runner.run_once() and seen == ["b", "other"]  # a 还忙，hook 任务让位给别的任务
    assert runner.run_once() is False  # 只剩 a 的钩子，仍然不领
    runner._hook_plugins.clear()
    assert runner.run_once() and runner.run_once() and runner.run_once() is False
    assert seen == ["b", "other", "a", "a"]

    for plugin in ("c", "d", "e"):
        _enqueue_hook(app, plugin, "h")
    runner._hook_plugins.update({901: "x", 902: "y"})  # 全局配额 2 已满
    assert runner.hook_concurrency == 2
    assert runner.run_once() is False
    runner._hook_plugins.pop(902)
    assert runner.run_once() and seen[-1] == "c"


def test_hook_concurrency_in_threads(tmp_path: Path) -> None:
    app = movie_app(tmp_path, {})
    lock = threading.Lock()
    active: dict[str, int] = {}
    peak = {"plugin": 0, "total": 0}

    def handler(payload: dict[str, Any], ctx: TaskContext) -> TaskResult:
        plugin = payload["plugin_id"]
        with lock:
            active[plugin] = active.get(plugin, 0) + 1
            peak["plugin"] = max(peak["plugin"], active[plugin])
            peak["total"] = max(peak["total"], sum(active.values()))
        time.sleep(0.05)
        with lock:
            active[plugin] -= 1
        return TaskResult()

    runner = TaskRunner(
        app.state.session_factory,
        app.state.services,
        {HOOK_TASK: handler},
        concurrency=4,
        poll_seconds=0.02,
    )
    for plugin in ("a", "a", "a", "b", "b", "c", "c"):
        _enqueue_hook(app, plugin, "h")
    runner.start()
    try:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and any(
            t.status in ("queued", "running") for t in tasks(app)
        ):
            time.sleep(0.05)
    finally:
        runner.stop()
    assert all(t.status == "succeeded" for t in tasks(app))
    assert peak["plugin"] == 1
    assert peak["total"] <= 2


# ---- 标签：权限、锁定、用户删除、NFO ----

TAG_HOOK = H(
    "t.tag",
    "scrape.finished",
    """
    result = host.add_tags(event.item_id, "keyword", ["插件标签"])
    dump(host, "added.json", result.model_dump(mode="json"))
    """,
    writes=("tags:keyword",),
)


def nfo_text(tmp_path: Path) -> str:
    return (tmp_path / "lib" / "matrix" / "movie.nfo").read_text(encoding="utf-8")


def origins(app: FastAPI, item_id: int, name: str) -> set[str]:
    with app.state.session_factory() as session:
        return set(
            session.scalars(
                select(ItemTagOrigin.origin)
                .join(Tag, Tag.id == ItemTagOrigin.tag_id)
                .where(ItemTagOrigin.item_id == item_id, Tag.name == name)
            )
        )


def test_hook_tag_rewrites_nfo_and_survives_rescrape(tmp_path: Path) -> None:
    app = movie_app(tmp_path, {"t.tags": [TAG_HOOK]})
    item_id = movie_item(app, tmp_path)
    enqueue_scrape(app, item_id)
    run(app)
    assert "<tag>插件标签</tag>" in nfo_text(tmp_path)
    assert origins(app, item_id, "插件标签") == {"t.tags"}
    assert dumped(app, "t.tags", "added.json")["applied"] == ["插件标签"]
    # 刮削后的整理写出 NFO 时标签还没加；钩子加完后又有一次静默重写。
    assert [json.loads(t.payload or "{}").get("silent") for t in tasks(app, "arrange")] == [
        None,
        True,
    ]
    # 再刮削一次：刮削只替换自己名下的标签，插件标签还在，NFO 里也还在。
    (tmp_path / "lib" / "matrix" / "movie.nfo").unlink()
    enqueue_scrape(app, item_id, force=True)
    run(app)
    assert "<tag>插件标签</tag>" in nfo_text(tmp_path)
    assert origins(app, item_id, "插件标签") == {"t.tags"}
    assert dumped(app, "t.tags", "added.json")["unchanged"] == ["插件标签"]
    assert all(t.status == "succeeded" for t in tasks(app))


def test_hook_rejects_undeclared_tag_kind(tmp_path: Path) -> None:
    app = movie_app(
        tmp_path,
        {
            "t.tags": [
                H(
                    "t.tag",
                    "scrape.finished",
                    'host.add_tags(event.item_id, "genre", ["越权"])',
                    writes=("tags:keyword",),
                )
            ]
        },
    )
    item_id = movie_item(app, tmp_path)
    enqueue_scrape(app, item_id)
    run(app)
    (task,) = tasks(app, HOOK_TASK)
    assert task.status == "failed" and "tags:genre" in (task.error or "")
    assert tasks(app, "scrape")[0].status == "succeeded"
    assert origins(app, item_id, "越权") == set()


def test_hook_cannot_remove_tags_it_did_not_add(tmp_path: Path) -> None:
    body = """
    view = host.get_item(event.item_id)
    names = list(view.tags.get("genre", ()))
    result = host.remove_tags(event.item_id, "genre", names)
    dump(host, "removed.json", {"names": names, "result": result.model_dump(mode="json")})
    """
    app = movie_app(
        tmp_path,
        {"t.tags": [H("t.tag", "scrape.finished", body, writes=("tags:genre",))]},
    )
    item_id = movie_item(app, tmp_path)
    enqueue_scrape(app, item_id)
    run(app)
    removed = dumped(app, "t.tags", "removed.json")
    assert removed["names"], "刮削应该给了类型标签"
    assert removed["result"]["applied"] == []
    assert removed["result"]["not_owned"] == removed["names"]
    with app.state.session_factory() as session:
        shown = session.scalars(
            select(Tag.name).where(Tag.tag_kind == "genre", Tag.name.in_(removed["names"]))
        ).all()
    assert sorted(shown) == sorted(removed["names"])


def test_hook_can_remove_its_own_tags(tmp_path: Path) -> None:
    body = """
    host.add_tags(event.item_id, "keyword", ["临时"])
    result = host.remove_tags(event.item_id, "keyword", ["临时"])
    dump(host, "removed.json", result.model_dump(mode="json"))
    """
    app = movie_app(
        tmp_path,
        {"t.tags": [H("t.tag", "scrape.finished", body, writes=("tags:keyword",))]},
    )
    item_id = movie_item(app, tmp_path)
    enqueue_scrape(app, item_id)
    run(app)
    assert dumped(app, "t.tags", "removed.json")["applied"] == ["临时"]
    assert origins(app, item_id, "临时") == set()
    assert "临时" not in nfo_text(tmp_path)


def test_hook_tag_skipped_for_locked_field(tmp_path: Path) -> None:
    app = movie_app(tmp_path, {"t.tags": [TAG_HOOK]})
    item_id = movie_item(app, tmp_path)
    with app.state.session_factory() as session:
        item = session.get(Item, item_id)
        assert item is not None
        item.locked_fields = json.dumps(["keywords"])
        session.commit()
    enqueue_scrape(app, item_id)
    run(app)
    assert dumped(app, "t.tags", "added.json") == {
        "applied": [],
        "unchanged": [],
        "locked": ["插件标签"],
        "removed_by_user": [],
        "not_owned": [],
    }
    assert origins(app, item_id, "插件标签") == set()
    assert "插件标签" not in nfo_text(tmp_path)


def test_hook_cannot_restore_tag_the_user_removed(tmp_path: Path) -> None:
    app = movie_app(tmp_path, {"t.tags": [TAG_HOOK]})
    item_id = movie_item(app, tmp_path)
    locks = app.state.services.locks
    with app.state.session_factory() as session:
        add_tags(session, locks, item_id, "keyword", ["插件标签"], "t.tags")
        set_item_tags(session, locks, item_id, "keyword", [])  # 用户手动删掉
    enqueue_scrape(app, item_id)
    run(app)
    assert dumped(app, "t.tags", "added.json")["removed_by_user"] == ["插件标签"]
    assert origins(app, item_id, "插件标签") == {"manual_removed"}
    assert "插件标签" not in nfo_text(tmp_path)


def test_hook_tag_limits(tmp_path: Path) -> None:
    app = movie_app(
        tmp_path,
        {
            "t.tags": [
                H(
                    "t.long",
                    "scrape.finished",
                    'host.add_tags(event.item_id, "keyword", ["长" * 100])',
                    writes=("tags:keyword",),
                ),
                H(
                    "t.many",
                    "scrape.finished",
                    'host.add_tags(event.item_id, "keyword", [str(i) for i in range(80)])',
                    writes=("tags:keyword",),
                ),
                H(
                    "t.calls",
                    "scrape.finished",
                    'for i in range(25):\n    host.add_tags(event.item_id, "keyword", [str(i)])',
                    writes=("tags:keyword",),
                ),
            ]
        },
    )
    item_id = movie_item(app, tmp_path, video=False)
    enqueue_scrape(app, item_id)
    run(app)
    errors = sorted((t.error or "") for t in tasks(app, HOOK_TASK))
    assert len(errors) == 3 and all("HookLimitError" in e for e in errors)
    assert any("64" in e for e in errors) and any("50" in e for e in errors)
    assert any("20 次" in e for e in errors)
    with app.state.session_factory() as session:
        count = session.scalar(
            select(ItemTagOrigin.tag_id).where(ItemTagOrigin.origin == "t.tags").limit(1)
        )
    assert count is not None  # 调用次数用完之前写进去的 20 个标签不会回滚
