import json
import sqlite3
import threading
from collections.abc import Callable
from pathlib import Path

import pytest
from alembic import command
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session, load_only

from mortis.catalog.install import ensure_builtin_types
from mortis.config.models import MovieScrapeConfig
from mortis.db.engine import create_db_engine, make_session_factory
from mortis.db.migrate import _alembic_config, upgrade_database
from mortis.db.models import Item, ItemTag, ItemTagOrigin, Library, Tag, TypeField, utc_now
from mortis.items.detail import read_value
from mortis.items.edit import edit_fields
from mortis.items.tags import (
    MANUAL,
    MANUAL_REMOVED,
    SCRAPE,
    add_tags,
    remove_tags,
    replace_scrape_tags,
    set_item_tags,
)
from mortis.output.nfo import build_movie_nfo, nfo_tags
from mortis.services import KeyedLocks

PLUGIN = "demo.rules"
OTHER = "demo.other"


def _item(session: Session) -> Item:
    ensure_builtin_types(session)
    now = utc_now()
    library = Library(data_type_id="movie", name="电影", created_at=now, updated_at=now)
    session.add(library)
    session.flush()
    item = Item(
        library_id=library.id,
        data_type_id="movie",
        kind="work",
        title="黑客帝国",
        created_at=now,
        updated_at=now,
    )
    session.add(item)
    session.commit()
    return item


def _shown(session: Session, item: Item, kind: str = "genre") -> list[str]:
    session.expire_all()
    return list(
        session.scalars(
            select(Tag.name)
            .join(ItemTag, ItemTag.tag_id == Tag.id)
            .where(ItemTag.item_id == item.id, Tag.tag_kind == kind)
            .order_by(ItemTag.position)
        )
    )


def _origins(session: Session, item: Item, kind: str = "genre") -> dict[str, set[str]]:
    session.expire_all()
    out: dict[str, set[str]] = {}
    for name, origin in session.execute(
        select(Tag.name, ItemTagOrigin.origin)
        .join(Tag, Tag.id == ItemTagOrigin.tag_id)
        .where(ItemTagOrigin.item_id == item.id, Tag.tag_kind == kind)
    ):
        out.setdefault(name, set()).add(origin)
    return out


def _assert_consistent(session: Session, item: Item) -> None:
    """item_tags 和来源表互相对得上：没有无来源的展示行，也没有无展示行的来源。"""
    session.expire_all()
    shown = {
        row.tag_id for row in session.scalars(select(ItemTag).where(ItemTag.item_id == item.id))
    }
    real: set[int] = set()
    removed: set[int] = set()
    for row in session.scalars(select(ItemTagOrigin).where(ItemTagOrigin.item_id == item.id)):
        (removed if row.origin == MANUAL_REMOVED else real).add(row.tag_id)
    assert shown == real
    assert not (removed & real)


def test_three_origins_do_not_overwrite_each_other(session: Session) -> None:
    item = _item(session)
    locks = KeyedLocks()
    replace_scrape_tags(session, item, "genre", ["动作", "科幻"])
    session.commit()
    add_tags(session, locks, item.id, "genre", ["赛博朋克", "动作"], PLUGIN)
    set_item_tags(session, locks, item.id, "genre", ["动作", "科幻", "赛博朋克", "我的最爱"])

    replace_scrape_tags(session, item, "genre", ["科幻", "犯罪"])
    session.commit()

    assert set(_shown(session, item)) == {"动作", "科幻", "犯罪", "赛博朋克", "我的最爱"}
    origins = _origins(session, item)
    assert origins["我的最爱"] == {MANUAL}
    assert origins["赛博朋克"] == {PLUGIN}
    assert origins["动作"] == {PLUGIN}  # 刮削撤掉了自己名下的，插件那份还在
    assert origins["科幻"] == {SCRAPE}
    assert origins["犯罪"] == {SCRAPE}
    _assert_consistent(session, item)


def test_scrape_replace_drops_only_its_own_and_unowned_rows(session: Session) -> None:
    item = _item(session)
    replace_scrape_tags(session, item, "genre", ["动作", "科幻"])
    replace_scrape_tags(session, item, "genre", ["科幻"])
    session.commit()
    assert _shown(session, item) == ["科幻"]
    _assert_consistent(session, item)


def test_locked_field_rejects_scrape_and_plugin(session: Session) -> None:
    item = _item(session)
    locks = KeyedLocks()
    replace_scrape_tags(session, item, "genre", ["动作"])
    add_tags(session, locks, item.id, "genre", ["赛博朋克"], PLUGIN)
    item.locked_fields = json.dumps(["genres"])
    session.commit()

    scraped = replace_scrape_tags(session, item, "genre", ["喜剧"])
    added = add_tags(session, locks, item.id, "genre", ["喜剧"], PLUGIN)
    removed = remove_tags(session, locks, item.id, "genre", ["赛博朋克"], PLUGIN)

    assert scraped.locked == ("喜剧",) and not scraped.changed
    assert added.locked == ("喜剧",) and not added.changed
    assert removed.locked == ("赛博朋克",) and not removed.changed
    assert set(_shown(session, item)) == {"动作", "赛博朋克"}

    set_item_tags(session, locks, item.id, "genre", ["动作", "人工"])  # 用户自己可以改
    assert set(_shown(session, item)) == {"动作", "人工"}
    _assert_consistent(session, item)


def test_plugin_cannot_remove_other_origins(session: Session) -> None:
    item = _item(session)
    locks = KeyedLocks()
    replace_scrape_tags(session, item, "genre", ["动作"])
    add_tags(session, locks, item.id, "genre", ["科幻"], OTHER)
    set_item_tags(session, locks, item.id, "genre", ["动作", "科幻", "手动"])

    result = remove_tags(session, locks, item.id, "genre", ["动作", "科幻", "手动", "没有"], PLUGIN)
    assert result.applied == () and result.not_owned == ("动作", "科幻", "手动", "没有")

    mine = add_tags(session, locks, item.id, "genre", ["动作", "新的"], PLUGIN)
    assert mine.applied == ("新的",) or mine.applied == ("动作", "新的")
    again = remove_tags(session, locks, item.id, "genre", ["动作", "新的"], PLUGIN)
    assert set(again.applied) == {"动作", "新的"}
    assert set(_shown(session, item)) == {"动作", "科幻", "手动"}
    assert _origins(session, item)["动作"] == {SCRAPE}
    _assert_consistent(session, item)


def test_reserved_origins_are_refused(session: Session) -> None:
    item = _item(session)
    for origin in (SCRAPE, MANUAL, MANUAL_REMOVED, ""):
        with pytest.raises(ValueError):
            add_tags(session, KeyedLocks(), item.id, "genre", ["x"], origin)


def test_user_removal_blocks_plugin_and_scrape(session: Session) -> None:
    item = _item(session)
    locks = KeyedLocks()
    replace_scrape_tags(session, item, "genre", ["动作", "科幻"])
    add_tags(session, locks, item.id, "genre", ["动作"], PLUGIN)
    session.commit()

    set_item_tags(session, locks, item.id, "genre", ["科幻"])  # 用户删掉「动作」

    assert _shown(session, item) == ["科幻"]
    assert _origins(session, item)["动作"] == {MANUAL_REMOVED}
    added = add_tags(session, locks, item.id, "genre", ["动作", "新"], PLUGIN)
    assert added.removed_by_user == ("动作",) and added.applied == ("新",)
    scraped = replace_scrape_tags(session, item, "genre", ["动作", "科幻"])
    session.commit()
    assert scraped.removed_by_user == ("动作",)
    assert set(_shown(session, item)) == {"科幻", "新"}

    set_item_tags(session, locks, item.id, "genre", ["科幻", "新", "动作"])  # 用户自己加回来
    assert set(_shown(session, item)) == {"科幻", "新", "动作"}
    assert _origins(session, item)["动作"] == {MANUAL}
    _assert_consistent(session, item)


def test_scrape_reorder_does_not_disturb_plugin_tag_order(session: Session) -> None:
    item = _item(session)
    locks = KeyedLocks()
    replace_scrape_tags(session, item, "genre", ["甲", "乙"])
    add_tags(session, locks, item.id, "genre", ["丙", "丁", "戊"], PLUGIN)
    assert _shown(session, item) == ["甲", "乙", "丙", "丁", "戊"]

    replace_scrape_tags(session, item, "genre", ["乙", "甲"])
    session.commit()
    assert _shown(session, item) == ["乙", "甲", "丙", "丁", "戊"]


def test_union_shows_one_tag_for_same_name(session: Session) -> None:
    item = _item(session)
    locks = KeyedLocks()
    replace_scrape_tags(session, item, "keyword", ["Cyberpunk"])
    add_tags(session, locks, item.id, "keyword", ["Cyberpunk", "AI"], PLUGIN)
    assert _shown(session, item, "keyword") == ["Cyberpunk", "AI"]
    assert len(session.scalars(select(Tag).where(Tag.name == "Cyberpunk")).all()) == 1


def test_nfo_uses_union(session: Session) -> None:
    item = _item(session)
    locks = KeyedLocks()
    replace_scrape_tags(session, item, "keyword", ["Cyberpunk"])
    add_tags(session, locks, item.id, "keyword", ["cyberpunk", "AI"], PLUGIN)
    set_item_tags(session, locks, item.id, "keyword", ["Cyberpunk", "cyberpunk", "AI", "手动"])

    assert nfo_tags(session, item) == ["Cyberpunk", "AI", "手动"]
    nfo = build_movie_nfo(session, item, MovieScrapeConfig()).decode()
    assert nfo.count("<tag>") == 3 and "<tag>手动</tag>" in nfo


def test_edit_fields_goes_through_service(session: Session) -> None:
    item = _item(session)
    locks = KeyedLocks()
    replace_scrape_tags(session, item, "genre", ["动作"])
    add_tags(session, locks, item.id, "genre", ["赛博朋克"], PLUGIN)
    session.commit()

    edit_fields(session, item, {"genres": ["赛博朋克", "喜剧"], "overview": "新"}, locks)

    assert _shown(session, item) == ["赛博朋克", "喜剧"]
    origins = _origins(session, item)
    assert origins["动作"] == {MANUAL_REMOVED}
    assert origins["喜剧"] == {MANUAL}
    assert origins["赛博朋克"] == {PLUGIN}
    assert json.loads(item.locked_fields) == ["overview", "genres"] or set(
        json.loads(item.locked_fields)
    ) == {"overview", "genres"}
    _assert_consistent(session, item)


def test_failed_write_rolls_back_both_steps(session: Session, monkeypatch) -> None:
    item = _item(session)
    locks = KeyedLocks()
    replace_scrape_tags(session, item, "genre", ["动作"])
    session.commit()
    import mortis.items.tags as tags

    def boom(*_args, **_kwargs) -> None:
        raise RuntimeError("注入失败")

    monkeypatch.setattr(tags, "_renumber", boom)
    with pytest.raises(RuntimeError):
        add_tags(session, locks, item.id, "genre", ["新"], PLUGIN)
    monkeypatch.undo()
    assert _shown(session, item) == ["动作"]
    assert _origins(session, item) == {"动作": {SCRAPE}}
    _assert_consistent(session, item)


def test_nfo_rewrite_only_after_commit(session: Session, monkeypatch) -> None:
    item = _item(session)
    calls: list[bool] = []

    def record(inner: Session, _runner: object, _item: Item) -> None:
        calls.append(inner.in_transaction() and bool(inner.new or inner.dirty))

    monkeypatch.setattr("mortis.output.service.rewrite_if_arranged", record)
    runner = object()
    add_tags(session, KeyedLocks(), item.id, "genre", ["新"], PLUGIN, runner=runner)  # type: ignore[arg-type]
    add_tags(session, KeyedLocks(), item.id, "genre", ["新"], PLUGIN, runner=runner)  # type: ignore[arg-type]
    assert calls == [False]  # 没有变化的写入不重写


def test_concurrent_writers_keep_every_tag_and_no_orphans(engine: Engine) -> None:
    factory = make_session_factory(engine)
    with factory() as setup:
        item_id = _item(setup).id
    locks = KeyedLocks()
    errors: list[BaseException] = []

    def worker(job: Callable[[Session], object]) -> threading.Thread:
        def run() -> None:
            try:
                with factory() as own:
                    job(own)
            except BaseException as error:  # noqa: BLE001
                errors.append(error)

        return threading.Thread(target=run)

    def plugin(origin: str, prefix: str) -> Callable[[Session], object]:
        def job(own: Session) -> None:
            for index in range(8):
                add_tags(own, locks, item_id, "genre", [f"{prefix}{index}"], origin)

        return job

    def scrape(own: Session) -> None:
        for round_no in range(4):
            with locks.hold("catalog-write"):
                item = own.get(Item, item_id)
                assert item is not None
                replace_scrape_tags(own, item, "genre", [f"刮{round_no}", "刮共同"])
                own.commit()

    def manual(own: Session) -> None:
        for index in range(4):
            set_item_tags(
                own, locks, item_id, "keyword", [f"手{index}"] + [f"手{n}" for n in range(index)]
            )

    jobs = [plugin("demo.a", "甲"), plugin("demo.b", "乙"), scrape, manual]
    threads = [worker(job) for job in jobs]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []

    with factory() as check:
        item = check.get(Item, item_id)
        assert item is not None
        names = set(_shown(check, item))
        assert {f"甲{i}" for i in range(8)} <= names
        assert {f"乙{i}" for i in range(8)} <= names
        _assert_consistent(check, item)
        positions = check.scalars(
            select(ItemTag.position)
            .join(Tag, Tag.id == ItemTag.tag_id)
            .where(ItemTag.item_id == item_id, Tag.tag_kind == "genre")
        ).all()
        assert sorted(positions) == list(range(len(positions)))
        assert set(_shown(check, item, "keyword")) == {"手0", "手1", "手2", "手3"}


# 迁移 ------------------------------------------------------------------------


def _old_database(tmp_path: Path) -> Path:
    db_path = tmp_path / "old.db"
    command.upgrade(_alembic_config(db_path), "0004")
    engine = create_db_engine(db_path)
    try:
        with make_session_factory(engine)() as session:
            ensure_builtin_types(session)
            session.commit()
        # 0004 还没有后来加上的列。用当前模型插入会把新列写进去，所以这里按当时的表写。
        now = utc_now()
        with engine.begin() as conn:
            conn.execute(
                text(
                    "insert into libraries (data_type_id, name, created_at, updated_at) "
                    "values ('movie', '电影', :now, :now)"
                ),
                {"now": now},
            )
            library_id = conn.execute(text("select id from libraries")).scalar_one()
            for number, (locked, sources) in enumerate(
                [
                    ([], {}),  # 无法判断 → scrape
                    (["genres"], {}),  # 锁定 → manual
                    ([], {"genres": "manual"}),  # 手改记录 → manual
                ],
                start=1,
            ):
                conn.execute(
                    text(
                        "insert into items ("
                        "library_id, data_type_id, kind, title, locked_fields, field_sources, "
                        "created_at, updated_at"
                        ") values ("
                        ":library_id, 'movie', 'work', :title, :locked, :sources, :now, :now)"
                    ),
                    {
                        "library_id": library_id,
                        "title": f"片{number}",
                        "locked": json.dumps(locked),
                        "sources": json.dumps(sources),
                        "now": now,
                    },
                )
        with make_session_factory(engine)() as session:
            genre = Tag(tag_kind="genre", name="动作")
            keyword = Tag(tag_kind="keyword", name="赛博朋克")
            session.add_all([genre, keyword])
            session.flush()
            for item_id in (1, 2, 3):
                session.add(ItemTag(item_id=item_id, tag_id=genre.id, position=0))
                session.add(ItemTag(item_id=item_id, tag_id=keyword.id, position=0))
            session.commit()
    finally:
        engine.dispose()
    return db_path


def _snapshot(db_path: Path) -> dict[int, tuple[list, list, bytes]]:
    engine = create_db_engine(db_path)
    out = {}
    try:
        with make_session_factory(engine)() as session:
            # 快照要能在升级前的库上读。当时还没有后来加的列，不能按当前模型整行选出。
            for item in session.scalars(
                select(Item)
                .options(
                    load_only(
                        Item.id,
                        Item.library_id,
                        Item.data_type_id,
                        Item.parent_id,
                        Item.kind,
                        Item.index_number,
                        Item.title,
                        Item.original_title,
                        Item.sort_title,
                        Item.subtitle,
                        Item.overview,
                        Item.release_date,
                        Item.poster_path,
                        Item.fields,
                        Item.field_sources,
                        Item.locked_fields,
                        Item.created_at,
                        Item.updated_at,
                    )
                )
                .order_by(Item.id)
            ):
                fields = list(
                    session.scalars(
                        select(TypeField).where(
                            TypeField.data_type_id == "movie",
                            TypeField.applies_to == "work",
                            TypeField.stored_in == "tags",
                        )
                    )
                )
                shown = [
                    read_value(session, item, field)
                    for field in sorted(fields, key=lambda f: f.field_key)
                ]
                out[item.id] = (
                    shown,
                    nfo_tags(session, item),
                    build_movie_nfo(session, item, MovieScrapeConfig()),
                )
    finally:
        engine.dispose()
    return out


def test_upgrade_backfills_origins_and_keeps_display_and_nfo(tmp_path: Path) -> None:
    db_path = _old_database(tmp_path)
    before = _snapshot(db_path)

    backup = upgrade_database(db_path)
    assert backup is not None and backup.is_file()

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "select item_id, origin from item_tag_origins order by item_id, tag_id"
        ).fetchall()
        assert conn.execute("select count(*) from item_tags").fetchone() == (6,)
    assert rows == [
        (1, "scrape"),
        (1, "scrape"),
        (2, "manual"),  # 类型字段被锁定
        (2, "scrape"),  # 关键词字段没锁
        (3, "manual"),  # 类型字段有手改记录
        (3, "scrape"),
    ]
    assert _snapshot(db_path) == before


def test_upgraded_cascade_and_foreign_keys(tmp_path: Path) -> None:
    db_path = _old_database(tmp_path)
    upgrade_database(db_path)
    engine = create_db_engine(db_path)
    try:
        with engine.begin() as conn:
            conn.execute(text("delete from item_tags where item_id = 1"))
            conn.execute(text("delete from items where id = 1"))
            assert (
                conn.execute(
                    text("select count(*) from item_tag_origins where item_id = 1")
                ).scalar_one()
                == 0
            )
            with pytest.raises(Exception, match="FOREIGN KEY"):
                conn.execute(text("insert into item_tag_origins values (99, 1, 'scrape')"))
    finally:
        engine.dispose()


def test_failed_backfill_rolls_back_whole_upgrade(tmp_path: Path) -> None:
    db_path = _old_database(tmp_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute("insert into item_tags (item_id, tag_id, position) values (1, 4242, 0)")
    conn.close()

    with pytest.raises(Exception, match="回填数量不对"):
        upgrade_database(db_path)

    with sqlite3.connect(db_path) as conn:
        names = {row[0] for row in conn.execute("select name from sqlite_master")}
        assert "item_tag_origins" not in names
        assert conn.execute("select version_num from alembic_version").fetchone() == ("0004",)
        assert conn.execute("select count(*) from item_tags").fetchone() == (7,)
    conn.close()
    assert list(tmp_path.glob("old.db.pre-migrate-*.bak"))


def test_downgrade_drops_only_origin_table(tmp_path: Path) -> None:
    db_path = _old_database(tmp_path)
    before = _snapshot(db_path)
    upgrade_database(db_path)

    command.downgrade(_alembic_config(db_path), "0004")

    with sqlite3.connect(db_path) as conn:
        names = {row[0] for row in conn.execute("select name from sqlite_master")}
        assert "item_tag_origins" not in names
        assert conn.execute("select count(*) from item_tags").fetchone() == (6,)
    conn.close()
    assert _snapshot(db_path) == before
