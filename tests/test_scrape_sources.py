import dataclasses
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from mortis.api.app import create_app
from mortis.catalog.install import ensure_builtin_types
from mortis.config.models import Secrets, TvOutputConfig, TvScrapeConfig
from mortis.db.models import (
    Item,
    ItemExternalId,
    ItemRating,
    ItemSourceRecord,
    Library,
    SourceOrder,
    Task,
    utc_now,
)
from mortis.scrape.apply import field_orders, level_fields, plan_values
from mortis.settings import Settings
from mortis.sources import builtin
from mortis.sources.builtin import (
    FetchRequest,
    builtin_ids,
    default_orders,
    seed_builtin_orders,
)
from mortis.tasks import repo
from tests.fakes import FakeTmdb, load_fixture

LOCAL = "http://127.0.0.1"


def _rating(source: str, value: float) -> dict[str, Any]:
    return {"source": source, "rating": value, "max_rating": 10.0, "vote_count": 1}


class FakeFetcher:
    """测试用的来源：按给定的候选值返回，记下请求次数。"""

    def __init__(self, values: dict[str, Any], error: Exception | None = None) -> None:
        self.values = values
        self.error = error
        self.calls = 0

    def fetch(self, request: FetchRequest) -> dict[str, Any] | None:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return {"values": self.values, "ids": dict(request.ids)}

    def candidates(self, raw: dict[str, Any], request: FetchRequest) -> dict[str, Any]:
        return dict(raw["values"])


def _install(monkeypatch: pytest.MonkeyPatch, source_id: str, fetcher: FakeFetcher) -> None:
    source = dataclasses.replace(builtin.BY_ID[source_id], fetcher=fetcher)
    monkeypatch.setitem(builtin.BY_ID, source_id, source)


# 合并和默认顺序


def test_ratings_merge_in_source_order(session: Session) -> None:
    ensure_builtin_types(session)
    fields = [f for f in level_fields(session, "movie", "work") if f.field_key == "ratings"]
    candidates = {
        "tmdb": {"ratings": [_rating("tmdb", 8.2), _rating("imdb", 7.0)]},
        "fanart": {"ratings": []},
        "imdb": {"ratings": [_rating("imdb", 8.7)]},
        "ratings_rt": {"ratings": [_rating("tomatometerallcritics", 88)]},
    }

    plan = plan_values(fields, candidates, {"": ["fanart", "imdb", "ratings_rt", "tmdb"]}, set())
    pick = plan["ratings"]
    assert pick is not None and pick.source == "imdb"
    assert [(r["source"], r["rating"]) for r in pick.value] == [
        ("imdb", 8.7),
        ("tomatometerallcritics", 88),
        ("tmdb", 8.2),
    ]

    plan = plan_values(fields, candidates, {"": ["tmdb", "imdb", "ratings_rt"]}, set())
    pick = plan["ratings"]
    assert pick is not None and pick.source == "tmdb"
    assert [(r["source"], r["rating"]) for r in pick.value] == [
        ("tmdb", 8.2),
        ("imdb", 7.0),
        ("tomatometerallcritics", 88),
    ]

    empty = plan_values(fields, {"fanart": {"ratings": []}}, ["fanart"], set())
    assert empty["ratings"] is None


def test_default_orders() -> None:
    assert default_orders("movie") == {"": ["tmdb", "fanart", "imdb", "ratings_rt"]}
    tv = default_orders("tv")
    assert tv[""] == ["tmdb", "tvdb", "fanart", "imdb", "ratings_rt"]
    assert tv["title"] == ["tmdb", "fanart", "imdb", "ratings_rt"]
    assert "poster" not in tv and "ratings" not in tv
    assert default_orders("anime") == tv


def test_tvdb_only_feeds_images_by_default(session: Session) -> None:
    ensure_builtin_types(session)
    seed_builtin_orders(session)
    orders = field_orders(session, "tv", builtin_ids("tv"))
    assert "tvdb" not in orders["title"] and "tvdb" not in orders["overview"]
    assert "tvdb" not in orders["genres"]
    assert orders.get("poster", orders[""])[:2] == ["tmdb", "tvdb"]
    assert "tvdb" in orders.get("ratings", orders[""])

    fields = level_fields(session, "tv", "work")
    poster = [{"url": "http://img/tvdb.jpg"}]
    plan = plan_values(
        fields,
        {
            "tmdb": {"title": "TMDB 名"},
            "tvdb": {"title": "TVDB 名", "overview": "x", "poster": poster},
        },
        orders,
        set(),
    )
    assert plan["title"] is not None and plan["title"].source == "tmdb"
    assert plan["overview"] is None
    assert plan["poster"] is not None and plan["poster"].source == "tvdb"


def test_seed_keeps_existing_whole_order(session: Session) -> None:
    ensure_builtin_types(session)
    session.add(SourceOrder(data_type_id="tv", field_key="", source="tmdb", position=0))
    session.flush()
    seed_builtin_orders(session)
    seed_builtin_orders(session)
    whole = session.scalars(
        select(SourceOrder.source)
        .where(SourceOrder.data_type_id == "tv", SourceOrder.field_key == "")
        .order_by(SourceOrder.position)
    ).all()
    assert whole == ["tmdb", "tvdb", "fanart", "imdb", "ratings_rt"]
    orders = field_orders(session, "tv", builtin_ids("tv"))
    assert orders[""] == ["tmdb", "tvdb", "fanart", "imdb", "ratings_rt"]
    assert "tvdb" not in orders["title"]
    assert "tvdb" in orders.get("poster", orders[""])


def test_seed_respects_field_reset_to_whole(session: Session) -> None:
    ensure_builtin_types(session)
    seed_builtin_orders(session)
    session.execute(
        delete(SourceOrder).where(
            SourceOrder.data_type_id == "tv", SourceOrder.field_key == "title"
        )
    )
    session.flush()
    seed_builtin_orders(session)
    title_rows = session.scalar(
        select(func.count()).where(
            SourceOrder.data_type_id == "tv", SourceOrder.field_key == "title"
        )
    )
    assert title_rows == 0
    orders = field_orders(session, "tv", builtin_ids("tv"))
    assert "tvdb" in orders.get("title", orders[""])


# 接口


@pytest.fixture
def fake() -> FakeTmdb:
    fake = FakeTmdb()
    fake.add("/3/movie/603", load_fixture("movie_603.json"))
    fake.add("/3/collection/2344", load_fixture("collection_2344.json"))
    fake.add("/3/collection/2344/images", load_fixture("collection_2344_images.json"))
    fake.add("/3/tv/80968", load_fixture("tv_80968.json"))
    fake.add("/3/tv/80968/season/1", load_fixture("tv_80968_season_1.json"))
    fake.add("/3/tv/80968/season/2", load_fixture("tv_80968_season_1.json"))
    fake.add("/3/tv/80968/season/3", load_fixture("tv_80968_season_3.json"))
    return fake


@pytest.fixture
def app(tmp_path: Path, fake: FakeTmdb) -> FastAPI:
    app = create_app(
        Settings(data_dir=tmp_path / "data"), http_transport=fake.transport, start_worker=False
    )
    app.state.config_store.save_secrets(Secrets(sources={"tmdb": {"api_key": "k"}}))
    app.state.config_store.save_section(
        "types.tv", TvScrapeConfig(output=TvOutputConfig(organize_after_scrape=False))
    )
    return app


def test_source_orders_api_lists_builtin_sources(app: FastAPI) -> None:
    client = TestClient(app, base_url=LOCAL)
    body = client.get("/api/data-types/movie/source-orders").json()
    assert [(s["id"], s["available"], s["reason"]) for s in body["sources"]] == [
        ("tmdb", True, None),
        ("fanart", False, "还没实现"),
        ("imdb", False, "还没实现"),
        ("ratings_rt", False, "还没实现"),
    ]
    assert [e["source"] for e in body["whole"]] == ["tmdb", "fanart", "imdb", "ratings_rt"]

    tv = client.get("/api/data-types/tv/source-orders").json()
    assert [s["name"] for s in tv["sources"]] == ["TMDB", "TVDB", "Fanart.tv", "IMDb", "烂番茄"]
    fields = {f["key"]: f for f in tv["fields"]}
    assert fields["title"]["custom"] is True
    assert {"source": "tvdb", "excluded": True} in fields["title"]["order"]
    assert fields["poster"]["custom"] is False

    app.state.config_store.save_secrets(Secrets())
    listed = client.get("/api/sources", params={"data_type": "movie"}).json()
    assert listed[0] == {
        "id": "tmdb",
        "name": "TMDB",
        "data_type": "movie",
        "builtin": True,
        "available": False,
        "reason": "Key 未填",
    }


def test_source_orders_api_write_and_reset(app: FastAPI) -> None:
    client = TestClient(app, base_url=LOCAL)
    changed = client.put(
        "/api/data-types/tv/source-orders",
        json={"orders": {"": [{"source": "fanart"}, {"source": "tmdb"}], "title": None}},
    ).json()
    assert [e["source"] for e in changed["whole"]] == [
        "fanart",
        "tmdb",
        "tvdb",
        "imdb",
        "ratings_rt",
    ]
    fields = {f["key"]: f for f in changed["fields"]}
    assert fields["title"]["custom"] is False

    reset = client.post("/api/data-types/tv/source-orders/reset").json()
    assert [e["source"] for e in reset["whole"]] == ["tmdb", "tvdb", "fanart", "imdb", "ratings_rt"]
    fields = {f["key"]: f for f in reset["fields"]}
    assert fields["title"]["custom"] is True
    assert {"source": "tvdb", "excluded": True} in fields["title"]["order"]


# 刮削


def _run(app: FastAPI, item_id: int, force: bool = False) -> Task:
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


def _work(app: FastAPI, data_type_id: str, tmdb_id: int, title: str) -> int:
    with app.state.session_factory() as session:
        now = utc_now()
        library = Library(data_type_id=data_type_id, name=title, created_at=now, updated_at=now)
        session.add(library)
        session.flush()
        item = Item(
            library_id=library.id,
            data_type_id=data_type_id,
            kind="work",
            title=title,
            created_at=now,
            updated_at=now,
        )
        session.add(item)
        session.flush()
        session.add(
            ItemExternalId(item_id=item.id, provider="tmdb", external_id=str(tmdb_id), is_primary=1)
        )
        session.commit()
        return item.id


def _outcomes(task: Task) -> list[tuple[str, str]]:
    return [(s["id"], s["outcome"]) for s in json.loads(task.result or "{}")["sources"]]


def _ratings(app: FastAPI, item_id: int) -> dict[str, float]:
    with app.state.session_factory() as session:
        return dict(
            session.execute(
                select(ItemRating.source, ItemRating.rating).where(ItemRating.item_id == item_id)
            ).all()
        )


def test_extra_source_merges_ratings_and_keeps_its_snapshot(
    app: FastAPI, fake: FakeTmdb, monkeypatch: pytest.MonkeyPatch
) -> None:
    item_id = _work(app, "movie", 603, "The Matrix")
    first = _run(app, item_id)
    assert first.status == "succeeded", first.error
    assert _outcomes(first) == [("tmdb", "ok"), ("tmdb_collection", "ok")]
    movie_requests = fake.paths().count("/3/movie/603")

    imdb = FakeFetcher({"ratings": [_rating("imdb", 8.7), _rating("tmdb", 1.0)]})
    _install(monkeypatch, "imdb", imdb)
    second = _run(app, item_id)
    assert second.status == "succeeded", second.error
    assert _outcomes(second) == [("tmdb", "cached"), ("imdb", "ok"), ("tmdb_collection", "cached")]
    assert fake.paths().count("/3/movie/603") == movie_requests
    assert imdb.calls == 1
    assert _ratings(app, item_id) == {"tmdb": 8.2, "imdb": 8.7}
    with app.state.session_factory() as session:
        record = session.get(ItemSourceRecord, (item_id, "imdb"))
        assert record is not None and json.loads(record.raw)["ids"]["tmdb"] == "603"
        assert session.get(ItemSourceRecord, (item_id, "tmdb")) is not None
        item = session.get(Item, item_id)
        assert item is not None and json.loads(item.field_sources)["ratings"] == "tmdb"

    third = _run(app, item_id)
    assert ("imdb", "cached") in _outcomes(third) and imdb.calls == 1
    _run(app, item_id, force=True)
    assert imdb.calls == 2
    assert fake.paths().count("/3/movie/603") == movie_requests + 1


def test_failing_extra_source_does_not_fail_scrape(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install(monkeypatch, "fanart", FakeFetcher({}, error=RuntimeError("服务挂了")))
    item_id = _work(app, "movie", 603, "The Matrix")
    task = _run(app, item_id)
    assert task.status == "succeeded", task.error
    sources = json.loads(task.result or "{}")["sources"]
    failed = next(s for s in sources if s["id"] == "fanart")
    assert failed["outcome"] == "failed" and "服务挂了" in failed["detail"]
    with app.state.session_factory() as session:
        assert session.get(ItemSourceRecord, (item_id, "fanart")) is None
        item = session.get(Item, item_id)
        assert item is not None and item.title == "黑客帝国"


def test_primary_failure_fails_scrape(app: FastAPI, fake: FakeTmdb) -> None:
    fake.add("/3/movie/603", {"status_message": "boom"}, status=500)
    item_id = _work(app, "movie", 603, "The Matrix")
    task = _run(app, item_id)
    assert task.status == "failed" and "500" in (task.error or "")


def test_tvdb_text_is_excluded_but_ratings_follow_whole_order(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    tvdb = FakeFetcher({"title": "TVDB 剧名", "ratings": [_rating("tvdb", 9.0)]})
    _install(monkeypatch, "tvdb", tvdb)
    client = TestClient(app, base_url=LOCAL)
    response = client.put(
        "/api/data-types/tv/source-orders",
        json={"orders": {"": [{"source": "tvdb"}, {"source": "tmdb"}]}},
    )
    assert response.status_code == 200
    show_id = _work(app, "tv", 80968, "Narcos: Mexico")
    task = _run(app, show_id)
    assert task.status == "succeeded", task.error
    assert _outcomes(task)[:2] == [("tmdb", "ok"), ("tvdb", "ok")]
    with app.state.session_factory() as session:
        show = session.get(Item, show_id)
        assert show is not None and show.title == "毒枭：墨西哥"
        origin = json.loads(show.field_sources)
        assert origin["title"] == "tmdb" and origin["ratings"] == "tvdb"
        assert session.get(ItemSourceRecord, (show_id, "tvdb")) is not None
    assert set(_ratings(app, show_id)) == {"tvdb", "tmdb"}
