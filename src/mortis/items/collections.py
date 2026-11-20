"""Galgame 手动合集。一部游戏只属于一个合集，扫描和刮削不改归属。"""

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from mortis.db.models import Item, ItemExternalId, ItemSourceRecord, Library, utc_now
from mortis.items.covers import COVER_MODES, member_order
from mortis.items.edit import ItemError
from mortis.scrape.service import drop_empty_collections

VNDB_RELATIONS = {"ser": "同一系列", "seq": "续作", "preq": "前传"}
BANGUMI_RELATIONS = frozenset({"前传", "续集", "番外篇", "主线"})


def list_collections(session: Session, library: Library) -> list[Item]:
    _require_galgame(library)
    return list(
        session.scalars(
            select(Item)
            .where(Item.library_id == library.id, Item.kind == "collection")
            .order_by(Item.title, Item.id)
        )
    )


def suggestions(session: Session, library: Library, work_ids: list[int]) -> list[dict[str, object]]:
    """来源快照里、本库已经有、但这次没勾上的相关游戏。默认不加入。"""
    _require_galgame(library)
    selected = set(work_ids)
    if not selected:
        return []
    wanted: dict[tuple[str, str], str] = {}
    records = session.scalars(
        select(ItemSourceRecord).where(ItemSourceRecord.item_id.in_(selected))
    )
    for record in records:
        try:
            raw = json.loads(record.raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(raw, dict):
            continue
        if record.source == "vndb":
            for rel in (raw.get("vn") or {}).get("relations") or []:
                relation = rel.get("relation")
                external_id = rel.get("id")
                if relation in VNDB_RELATIONS and external_id:
                    wanted.setdefault(("vndb", str(external_id)), VNDB_RELATIONS[relation])
        elif record.source == "bangumi":
            for rel in raw.get("related") or []:
                relation = rel.get("relation")
                external_id = rel.get("id")
                if relation in BANGUMI_RELATIONS and external_id is not None:
                    wanted.setdefault(("bangumi", str(external_id)), str(relation))
    if not wanted:
        return []
    rows = session.execute(
        select(Item, ItemExternalId)
        .join(ItemExternalId, ItemExternalId.item_id == Item.id)
        .where(Item.library_id == library.id, Item.kind == "work")
        .order_by(Item.title, Item.id)
    ).all()
    found: list[dict[str, object]] = []
    seen: set[int] = set()
    for item, external in rows:
        label = wanted.get((external.provider, external.external_id))
        if label is None or item.id in selected or item.id in seen:
            continue
        seen.add(item.id)
        found.append({"id": item.id, "title": item.title, "relation": label})
    return found


def assign(
    session: Session,
    library: Library,
    work_ids: list[int],
    *,
    title: str | None,
    collection_id: int | None,
) -> tuple[Item, int]:
    """新建或收入已有合集。已在别的合集里的游戏会移过来。空掉的原合集删掉。"""
    _require_galgame(library)
    if (title is None) == (collection_id is None):
        raise ItemError("要新建合集，或选择一个已有合集")
    works = _works(session, library, work_ids)
    if collection_id is None:
        name = (title or "").strip()
        if not name:
            raise ItemError("合集名不能为空")
        now = utc_now()
        collection = Item(
            library_id=library.id,
            data_type_id=library.data_type_id,
            kind="collection",
            title=name,
            fields=json.dumps({"cover_mode": "auto"}, ensure_ascii=False),
            created_at=now,
            updated_at=now,
        )
        session.add(collection)
        session.flush()
    else:
        collection = _collection(session, library, collection_id)
    moved = sum(1 for work in works if work.parent_id not in (None, collection.id))
    _place(session, collection, works)
    session.flush()
    drop_empty_collections(session, library.id)
    session.commit()
    return collection, moved


def add_members(session: Session, collection: Item, work_ids: list[int]) -> Item:
    library = _library_of(session, collection)
    works = _works(session, library, work_ids)
    _place(session, collection, works)
    session.flush()
    drop_empty_collections(session, library.id)
    session.commit()
    return collection


def remove_members(session: Session, collection: Item, work_ids: list[int]) -> None:
    unique = list(dict.fromkeys(work_ids))
    members = {member.id: member for member in _members(session, collection)}
    if not unique or any(work_id not in members for work_id in unique):
        raise ItemError("这些游戏不都在这个合集里", status_code=404)
    now = utc_now()
    for work_id in unique:
        work = members[work_id]
        work.parent_id = None
        work.index_number = None
        work.updated_at = now
    session.flush()
    drop_empty_collections(session, collection.library_id)
    session.commit()


def remove_member(session: Session, collection: Item, work_id: int) -> None:
    remove_members(session, collection, [work_id])


def reorder(session: Session, collection: Item, work_ids: list[int]) -> None:
    members = _members(session, collection)
    if set(work_ids) != {member.id for member in members} or len(work_ids) != len(set(work_ids)):
        raise ItemError("排序要包含这个合集里的全部游戏，不能多也不能少")
    by_id = {member.id: member for member in members}
    now = utc_now()
    for index, work_id in enumerate(work_ids, start=1):
        by_id[work_id].index_number = index
        by_id[work_id].updated_at = now
    collection.updated_at = now
    session.commit()


def dissolve(session: Session, collection: Item) -> int:
    """去掉归属并删除合集行。先清 parent_id，避免级联删掉游戏。"""
    library_id = collection.library_id
    for work in _members(session, collection):
        work.parent_id = None
        work.index_number = None
        work.updated_at = utc_now()
    session.flush()
    session.delete(collection)
    session.commit()
    return library_id


def rename(session: Session, collection: Item, title: str) -> None:
    name = title.strip()
    if not name:
        raise ItemError("合集名不能为空")
    collection.title = name
    collection.updated_at = utc_now()
    session.commit()


def set_cover_mode(session: Session, collection: Item, mode: str) -> None:
    if mode not in COVER_MODES:
        raise ItemError("封面方式只能是自动四宫格、单张或自选四张")
    try:
        fields = json.loads(collection.fields or "{}")
    except json.JSONDecodeError:
        fields = {}
    if not isinstance(fields, dict):
        fields = {}
    fields["cover_mode"] = mode
    collection.fields = json.dumps(fields, ensure_ascii=False)
    collection.updated_at = utc_now()
    session.commit()


def _require_galgame(library: Library) -> None:
    if library.data_type_id != "galgame":
        raise ItemError("只有 Galgame 库能手动建合集")


def _library_of(session: Session, collection: Item) -> Library:
    if collection.kind != "collection" or collection.data_type_id != "galgame":
        raise ItemError("只有 Galgame 合集能这样改")
    library = session.get(Library, collection.library_id)
    if library is None:
        raise ItemError("媒体库不存在", status_code=404)
    return library


def _collection(session: Session, library: Library, collection_id: int) -> Item:
    collection = session.get(Item, collection_id)
    if (
        collection is None
        or collection.kind != "collection"
        or collection.library_id != library.id
    ):
        raise ItemError("没有这个合集", status_code=404)
    return collection


def _works(session: Session, library: Library, work_ids: list[int]) -> list[Item]:
    unique = list(dict.fromkeys(work_ids))
    if not unique:
        raise ItemError("先选择游戏")
    found = {
        item.id: item
        for item in session.scalars(select(Item).where(Item.id.in_(unique)))
    }
    works: list[Item] = []
    for work_id in unique:
        work = found.get(work_id)
        if (
            work is None
            or work.library_id != library.id
            or work.kind != "work"
            or work.data_type_id != "galgame"
        ):
            raise ItemError("只能收入这个库里的游戏")
        works.append(work)
    return works


def _members(session: Session, collection: Item) -> list[Item]:
    return list(
        session.scalars(
            select(Item).where(Item.parent_id == collection.id).order_by(*member_order())
        )
    )


def _place(session: Session, collection: Item, works: list[Item]) -> None:
    """已在合集里的保持相对顺序，新游戏按这次给出的顺序接在后面。"""
    current = _members(session, collection)
    have = {member.id for member in current}
    ordered = [*current, *[work for work in works if work.id not in have]]
    now = utc_now()
    for index, work in enumerate(ordered, start=1):
        work.parent_id = collection.id
        work.index_number = index
        work.updated_at = now
    collection.updated_at = now
