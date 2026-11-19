import json
import textwrap
from pathlib import Path

import httpx2 as httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select

from mortis.api.app import create_app
from mortis.config.models import PluginTypeConfig
from mortis.db.models import (
    Item,
    ItemExternalId,
    ItemPerson,
    ItemRating,
    ItemSourceRecord,
    Library,
    Person,
    PersonExternalId,
    SourceOrder,
    Task,
    utc_now,
)
from mortis.scrape.images import ImageStore
from mortis.settings import Settings
from mortis.tasks import repo
from mortis.tasks.logs import task_log_path

DATA_TYPE = """
from mortis.plugin_api import DataTypePlugin, DataTypeSpec, FieldSpec

def column(key, label, value_type, column):
    return FieldSpec(field_key=key, label=label, value_type=value_type,
                     stored_in="items_column", column_name=column, detail_section="基本")

PLUGIN = DataTypePlugin(
    spec=DataTypeSpec(
        id="doujin",
        name="同人游戏",
        structure="item",
        fields=[
            column("title", "名称", "text", "title"),
            column("original_title", "原名", "text", "original_title"),
            column("overview", "简介", "longtext", "overview"),
            column("poster", "封面", "images", "poster_path"),
            FieldSpec(field_key="engine", label="引擎", value_type="text",
                      stored_in="fields", detail_section="基本"),
            FieldSpec(field_key="voice_actors", label="声优", value_type="people",
                      stored_in="people", person_role="voice", detail_section="人员"),
            FieldSpec(field_key="ratings", label="评分", value_type="rating",
                      stored_in="ratings", detail_section="基本"),
        ],
    ),
    default_orders={"": ["alpha", "beta"], "engine": ["beta"]},
)
"""

SOURCE = """
from mortis.plugin_api import Hit, SourcePlugin


class Source(SourcePlugin):
    id = "{id}"
    name = "{name}"
    data_type = "doujin"
    provider = "{id}"
    requests_per_second = 0

    def search(self, host, title):
        body = host.get_json("https://{id}.test/search", params={{"q": title}})
        return [Hit(**hit) for hit in body]

    def fetch(self, host, external_id):
        return host.get_json(f"https://{id}.test/item/{{external_id}}")

    def candidates(self, raw):
        return raw["values"]


PLUGIN = Source()
"""

DISABLED_SOURCE = """
from pydantic import BaseModel

from mortis.plugin_api import SourcePlugin


class Secrets(BaseModel):
    api_key: str = ""


class Source(SourcePlugin):
    id = "{id}"
    name = "{name}"
    data_type = "doujin"
    provider = "{id}"
    Secrets = Secrets
    requests_per_second = 0

    def enabled(self, host):
        return bool(host.secrets.api_key)

    def disabled_reason(self, host):
        return None if self.enabled(host) else "没填 {name} 的 api_key"

    def search(self, host, title):
        raise AssertionError("没启用的来源不该搜索")

    def fetch(self, host, external_id):
        raise AssertionError("没启用的来源不该请求")

    def candidates(self, raw):
        return {{}}


PLUGIN = Source()
"""

PNG = b"\x89PNG\r\n\x1a\nfake"


class FakeSites:
    def __init__(self) -> None:
        self.hits: dict[str, list[dict]] = {}
        self.items: dict[str, dict] = {}
        self.broken: set[str] = set()
        self.requests: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(str(request.url))
        site = request.url.host.split(".")[0]
        if request.url.host == "img.test":
            return httpx.Response(200, content=PNG, headers={"content-type": "image/png"})
        if site in self.broken:
            return httpx.Response(500)
        if request.url.path == "/search":
            return httpx.Response(200, json=self.hits.get(site, []))
        key = f"{site}{request.url.path}"
        if key in self.items:
            return httpx.Response(200, json=self.items[key])
        return httpx.Response(404)

    def count(self, prefix: str) -> int:
        return sum(1 for url in self.requests if url.startswith(prefix))


def _write(folder: Path, plugin_id: str, kind: str, code: str) -> None:
    target = folder / plugin_id
    target.mkdir(parents=True, exist_ok=True)
    (target / "plugin.toml").write_text(
        f'id = "{plugin_id}"\nname = "{plugin_id}"\nversion = "1"\nkind = "{kind}"\n'
        "[mortis]\napi_version = 1\n",
        encoding="utf-8",
    )
    (target / "__init__.py").write_text(textwrap.dedent(code), encoding="utf-8")


@pytest.fixture
def sites() -> FakeSites:
    sites = FakeSites()
    sites.hits["alpha"] = [{"external_id": "a1", "title": "星之梦", "year": 2010}]
    sites.items["alpha/item/a1"] = {
        "values": {
            "title": "星之梦",
            "original_title": "ほしのゆめ",
            "overview": "Alpha 简介",
            "poster": [{"url": "https://img.test/alpha.png"}],
            "engine": "Alpha 引擎",
            "voice_actors": [
                {"external_id": 7, "name": "花泽香菜", "character": "梦美", "photo_url": None}
            ],
            "ratings": [{"source": "alpha", "rating": 8.0, "max_rating": 10}],
        }
    }
    sites.hits["beta"] = [{"external_id": "b9", "title": "Hoshi no Yume", "aliases": ["星之梦"]}]
    sites.items["beta/item/b9"] = {
        "values": {
            "title": "Planetarian",
            "overview": "Beta 简介",
            "engine": "Beta 引擎",
            "voice_actors": [
                {"external_id": "s1", "name": "花泽香菜", "character": "梦美"},
                {"external_id": "", "name": "无编号"},
            ],
        }
    }
    return sites


@pytest.fixture
def app(tmp_path: Path, sites: FakeSites) -> FastAPI:
    plugins = tmp_path / "plugins"
    _write(plugins, "doujin", "data_type", DATA_TYPE)
    _write(plugins, "alpha", "source", SOURCE.format(id="alpha", name="Alpha"))
    _write(plugins, "beta", "source", SOURCE.format(id="beta", name="Beta"))
    return create_app(
        Settings(data_dir=tmp_path / "data", plugins_dir=plugins),
        http_transport=httpx.MockTransport(sites.handler),
        start_worker=False,
    )


@pytest.fixture
def item_id(app: FastAPI) -> int:
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
            release_date="2010-01-01",
            created_at=now,
            updated_at=now,
        )
        session.add(item)
        session.flush()
        session.add(ItemExternalId(item_id=item.id, provider="alpha", external_id="a1"))
        session.commit()
        return item.id


def _scrape(app: FastAPI, item_id: int, force: bool = False) -> Task:
    with app.state.session_factory() as session:
        task = repo.enqueue(session, "scrape", {"item_id": item_id, "force": force})
        session.commit()
        task_id = task.id
    app.state.task_runner.run_until_idle()
    with app.state.session_factory() as session:
        task = session.get(Task, task_id)
        assert task is not None
        session.expunge(task)
        return task


def test_fields_follow_their_own_source_order(app: FastAPI, item_id: int) -> None:
    task = _scrape(app, item_id)
    assert task.status == "succeeded", task.error
    with app.state.session_factory() as session:
        item = session.get(Item, item_id)
        assert item is not None
        assert item.title == "星之梦"
        assert item.overview == "Alpha 简介"
        assert json.loads(item.fields) == {"engine": "Beta 引擎"}
        sources = json.loads(item.field_sources)
        assert sources["title"] == "alpha" and sources["engine"] == "beta"
        assert ImageStore(app.state.settings.data_dir).resolve(item.poster_path) is not None
        ids = dict(
            session.execute(
                select(ItemExternalId.provider, ItemExternalId.external_id).where(
                    ItemExternalId.item_id == item_id
                )
            ).all()
        )
        assert ids == {"alpha": "a1", "beta": "b9"}
        assert session.get(ItemSourceRecord, (item_id, "alpha")) is not None
        assert session.get(ItemSourceRecord, (item_id, "beta")) is not None
        rating = session.get(ItemRating, (item_id, "alpha"))
        assert rating is not None and rating.rating == 8.0
        voice = session.execute(
            select(Person.name, PersonExternalId.provider, PersonExternalId.external_id)
            .join(ItemPerson, ItemPerson.person_id == Person.id)
            .join(PersonExternalId, PersonExternalId.person_id == Person.id)
            .where(ItemPerson.item_id == item_id)
        ).all()
        assert [tuple(row) for row in voice] == [("花泽香菜", "alpha", "7")]


def test_field_row_and_exclusion(app: FastAPI, item_id: int) -> None:
    with app.state.session_factory() as session:
        session.get(SourceOrder, ("doujin", "", "alpha")).position = 5
        session.add(
            SourceOrder(
                data_type_id="doujin",
                field_key="overview",
                source="beta",
                position=0,
                is_excluded=1,
            )
        )
        session.commit()
    assert _scrape(app, item_id).status == "succeeded"
    with app.state.session_factory() as session:
        item = session.get(Item, item_id)
        assert item is not None
        assert item.title == "Planetarian"
        assert item.overview == "Alpha 简介"


def test_locked_fields_kept(app: FastAPI, item_id: int) -> None:
    with app.state.session_factory() as session:
        item = session.get(Item, item_id)
        item.overview = "我写的"
        item.locked_fields = json.dumps(["overview"])
        session.commit()
    assert _scrape(app, item_id).status == "succeeded"
    with app.state.session_factory() as session:
        assert session.get(Item, item_id).overview == "我写的"


def test_snapshots_reused_unless_forced(app: FastAPI, item_id: int, sites: FakeSites) -> None:
    _scrape(app, item_id)
    before = sites.count("https://alpha.test/item") + sites.count("https://beta.test/")
    _scrape(app, item_id)
    after = sites.count("https://alpha.test/item") + sites.count("https://beta.test/")
    assert after == before
    _scrape(app, item_id, force=True)
    assert sites.count("https://alpha.test/item") == 2
    assert sites.count("https://beta.test/search") == 1


def test_failing_source_is_skipped(app: FastAPI, item_id: int, sites: FakeSites) -> None:
    sites.broken.add("beta")
    task = _scrape(app, item_id)
    assert task.status == "succeeded", task.error
    with app.state.session_factory() as session:
        item = session.get(Item, item_id)
        assert json.loads(item.fields) == {}
        assert session.get(ItemSourceRecord, (item_id, "beta")) is None


def test_low_score_source_not_bound(app: FastAPI, item_id: int, sites: FakeSites) -> None:
    sites.hits["beta"] = [{"external_id": "b2", "title": "完全不同的游戏"}]
    assert _scrape(app, item_id).status == "succeeded"
    with app.state.session_factory() as session:
        providers = set(
            session.scalars(
                select(ItemExternalId.provider).where(ItemExternalId.item_id == item_id)
            )
        )
        assert providers == {"alpha"}


def test_type_threshold_decides_binding(app: FastAPI, item_id: int, sites: FakeSites) -> None:
    """0.6 分的候选默认认不出；把该类型的阈值调到 0.5 后刮削会挂上它。"""
    sites.hits["beta"] = [{"external_id": "b6", "title": "星之梦外传序章"}]
    sites.items["beta/item/b6"] = {"values": {"title": "星之梦外传序章"}}

    def providers() -> set[str]:
        with app.state.session_factory() as session:
            return set(
                session.scalars(
                    select(ItemExternalId.provider).where(ItemExternalId.item_id == item_id)
                )
            )

    assert _scrape(app, item_id).status == "succeeded"
    assert providers() == {"alpha"}

    app.state.config_store.save_section("types.doujin", PluginTypeConfig(match_threshold=0.5))
    assert _scrape(app, item_id, force=True).status == "succeeded"
    assert providers() == {"alpha", "beta"}
    with app.state.session_factory() as session:
        assert session.get(ItemSourceRecord, (item_id, "beta")) is not None


def test_all_sources_failing_fails_task(app: FastAPI, item_id: int, sites: FakeSites) -> None:
    sites.broken.update({"alpha", "beta"})
    task = _scrape(app, item_id)
    assert task.status == "failed" and "所有来源" in (task.error or "")


def test_disabled_data_type_cannot_scrape(app: FastAPI, item_id: int) -> None:
    app.state.config_store.save_plugin("doujin", disabled=True)
    app.state.services.plugins.load(["doujin"])
    with app.state.session_factory() as session:
        app.state.services.plugins.sync_catalog(session)
        session.commit()
    task = _scrape(app, item_id)
    assert task.status == "failed" and "没有启用" in (task.error or "")


def _add_disabled_source(app: FastAPI) -> None:
    """再加一个没填密钥的来源，排在 alpha、beta 之后。"""
    plugins = app.state.settings.plugins_dir
    assert plugins is not None
    _write(plugins, "gamma", "source", DISABLED_SOURCE.format(id="gamma", name="Gamma"))
    app.state.services.plugins.load()
    with app.state.session_factory() as session:
        app.state.services.plugins.sync_catalog(session)
        session.commit()


def _sources(task: Task) -> list[dict]:
    assert task.status == "succeeded", task.error
    return json.loads(task.result or "{}")["sources"]


def test_result_records_ok_cached_and_skipped(app: FastAPI, item_id: int) -> None:
    _add_disabled_source(app)
    assert _sources(_scrape(app, item_id)) == [
        {"id": "alpha", "name": "Alpha", "outcome": "ok", "detail": None},
        {"id": "beta", "name": "Beta", "outcome": "ok", "detail": None},
        {"id": "gamma", "name": "Gamma", "outcome": "skipped", "detail": "没填 Gamma 的 api_key"},
    ]
    assert [(s["id"], s["outcome"]) for s in _sources(_scrape(app, item_id))] == [
        ("alpha", "cached"),
        ("beta", "cached"),
        ("gamma", "skipped"),
    ]


def test_result_records_not_found_and_failed(app: FastAPI, item_id: int, sites: FakeSites) -> None:
    with app.state.session_factory() as session:
        session.query(ItemExternalId).filter_by(item_id=item_id, provider="beta").delete()
        session.commit()
    sites.hits["beta"] = [{"external_id": "b2", "title": "完全不同的游戏"}]
    task = _scrape(app, item_id, force=True)
    missed = _sources(task)[1]
    assert (missed["id"], missed["outcome"]) == ("beta", "not_found")
    assert "星之梦" in missed["detail"] and "完全不同的游戏" in missed["detail"]
    assert "最高匹配度" in missed["detail"]
    # detail 和日志是同一句话
    log = task_log_path(app.state.settings.data_dir, task.id)
    assert any(missed["detail"] in line for line in log.read_text(encoding="utf-8").splitlines())

    sites.broken.add("beta")
    failed = _sources(_scrape(app, item_id, force=True))[1]
    assert (failed["id"], failed["outcome"]) == ("beta", "failed")
    assert failed["detail"] == "Beta 出错，跳过：Beta 返回 HTTP 500"
