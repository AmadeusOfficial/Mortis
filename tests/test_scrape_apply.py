import json
from collections.abc import Mapping

from sqlalchemy import select
from sqlalchemy.orm import Session

from mortis.catalog.install import ensure_builtin_types
from mortis.db.models import (
    Item,
    ItemImage,
    ItemPerson,
    ItemRating,
    ItemTag,
    Library,
    Person,
    PersonExternalId,
    Tag,
    utc_now,
)
from mortis.scrape.apply import (
    Pick,
    image_urls,
    level_fields,
    plan_values,
    source_order,
    write_plan,
)


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
        title="旧片名",
        created_at=now,
        updated_at=now,
    )
    session.add(item)
    session.flush()
    return item


CANDIDATES = {
    "title": "黑客帝国",
    "original_title": "The Matrix",
    "overview": "剧情",
    "runtime": 136,
    "genres": ["动作", "科幻", "动作"],
    "actors": [
        {
            "tmdb_id": 1,
            "name": "甲",
            "original_name": "A",
            "character": "Neo",
            "photo_url": "http://img/a.jpg",
        },
        {"tmdb_id": 2, "name": "乙", "original_name": None, "character": None, "photo_url": None},
    ],
    "ratings": [{"source": "tmdb", "rating": 8.2, "max_rating": 10.0, "vote_count": 3}],
    "poster": [{"url": "http://img/p.jpg", "language": "zh", "width": 1, "height": 2}],
    "fanart": [{"url": "http://img/f.jpg", "language": None, "width": 3, "height": 4}],
}
FILES = {
    "http://img/p.jpg": "aa/p.jpg",
    "http://img/f.jpg": "bb/f.jpg",
    "http://img/a.jpg": "cc/a.jpg",
}


def _apply(
    session: Session,
    item: Item,
    candidates: dict,
    skip: frozenset[str] = frozenset(),
    files: Mapping[str, str] = FILES,
) -> None:
    fields = level_fields(session, "movie", "work")
    plan = plan_values(fields, {"tmdb": candidates}, source_order(session, "movie"), set(skip))
    write_plan(session, item, fields, plan, files)
    session.flush()


def test_writes_every_storage(session: Session) -> None:
    item = _item(session)
    _apply(session, item, CANDIDATES)
    assert (item.title, item.original_title, item.overview) == ("黑客帝国", "The Matrix", "剧情")
    assert item.subtitle == "The Matrix"
    assert item.poster_path == "aa/p.jpg"
    assert json.loads(item.fields) == {"runtime": 136}
    sources = json.loads(item.field_sources)
    assert sources["title"] == "tmdb" and sources["poster"] == "tmdb"
    assert "tagline" not in sources
    genres = session.scalars(
        select(Tag.name)
        .join(ItemTag)
        .where(ItemTag.item_id == item.id, Tag.tag_kind == "genre")
        .order_by(ItemTag.position)
    ).all()
    assert genres == ["动作", "科幻"]
    rows = session.execute(
        select(Person.name, Person.photo_path, ItemPerson.character_name)
        .join(ItemPerson)
        .where(ItemPerson.item_id == item.id)
        .order_by(ItemPerson.position)
    ).all()
    assert [tuple(row) for row in rows] == [("甲", "cc/a.jpg", "Neo"), ("乙", None, None)]
    assert session.scalar(select(ItemRating.rating)) == 8.2
    fanart = session.scalar(select(ItemImage).where(ItemImage.image_kind == "fanart"))
    assert fanart is not None
    assert (fanart.file_path, fanart.source, fanart.url) == ("bb/f.jpg", "tmdb", "http://img/f.jpg")


def test_rescrape_reuses_people_and_tags_and_clears_missing(session: Session) -> None:
    item = _item(session)
    _apply(session, item, CANDIDATES)
    smaller = {**CANDIDATES, "genres": ["科幻"], "actors": CANDIDATES["actors"][:1]}
    del smaller["runtime"], smaller["fanart"]
    _apply(session, item, smaller)
    assert json.loads(item.fields) == {}
    assert "runtime" not in json.loads(item.field_sources)
    assert len(session.scalars(select(Person)).all()) == 2
    assert len(session.scalars(select(PersonExternalId)).all()) == 2
    first = session.scalar(
        select(PersonExternalId.person_id).where(PersonExternalId.external_id == "1")
    )
    assert session.scalars(select(ItemPerson.person_id)).all() == [first]
    assert session.scalar(select(ItemImage).where(ItemImage.image_kind == "fanart")) is None
    assert len(session.scalars(select(Tag).where(Tag.tag_kind == "genre")).all()) == 2


def test_skip_and_title_never_cleared(session: Session) -> None:
    item = _item(session)
    item.title = "我的片名"
    item.overview = "我的剧情"
    _apply(session, item, {"overview": "新剧情"}, skip=frozenset({"overview"}))
    assert item.title == "我的片名"
    assert item.overview == "我的剧情"
    assert "overview" not in json.loads(item.field_sources)


def test_failed_download_keeps_old_image(session: Session) -> None:
    item = _item(session)
    _apply(session, item, CANDIDATES)
    newer = {
        **CANDIDATES,
        "poster": [{"url": "http://img/new.jpg", "language": None, "width": 1, "height": 1}],
    }
    _apply(session, item, newer, files={})
    assert item.poster_path == "aa/p.jpg"
    assert json.loads(item.field_sources)["poster"] == "tmdb"


def test_image_urls_first_of_each_kind(session: Session) -> None:
    _item(session)
    fields = level_fields(session, "movie", "work")
    urls = image_urls(fields, {"tmdb": CANDIDATES}, skip={"fanart"})
    assert urls == ["http://img/p.jpg"]


def test_plan_prefers_order_and_ignores_empty(session: Session) -> None:
    _item(session)
    fields = [f for f in level_fields(session, "movie", "work") if f.field_key == "overview"]
    plan = plan_values(fields, {"a": {"overview": ""}, "b": {"overview": "乙"}}, ["a", "b"], set())
    assert plan == {"overview": Pick("b", "乙")}
