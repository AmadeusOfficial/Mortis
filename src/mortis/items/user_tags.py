"""用户自定义标签。存在 tags / item_tags 里，tag_kind 是 user:<数据类型>，按类型分开。

类型声明里没有这种 kind，刮削和来源插件不会写它，所以不用锁定。只给作品和系列打。
"""

from collections.abc import Iterable
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from mortis.db.models import DataType, Item, ItemTag, Tag, utc_now
from mortis.errors import UserError

PREFIX = "user:"
NAME_LIMIT = 40
TAGGABLE_KINDS = ("work", "collection")


class TagError(UserError):
    pass


def tag_kind(data_type_id: str) -> str:
    return PREFIX + data_type_id


def clean_name(raw: str) -> str:
    name = raw.strip()
    if not name:
        raise TagError("标签名不能为空")
    if len(name) > NAME_LIMIT:
        raise TagError(f"标签名最多 {NAME_LIMIT} 个字：{name[:NAME_LIMIT]}…")
    return name


def clean_names(raws: Iterable[str]) -> list[str]:
    """去首尾空白、不区分大小写去重，保留第一次出现的写法和顺序。"""
    names: dict[str, str] = {}
    for raw in raws:
        name = clean_name(raw)
        names.setdefault(name.casefold(), name)
    return list(names.values())


def item_tags(session: Session, item: Item) -> list[str]:
    return list(
        session.scalars(
            select(Tag.name)
            .join(ItemTag, ItemTag.tag_id == Tag.id)
            .where(ItemTag.item_id == item.id, Tag.tag_kind == tag_kind(item.data_type_id))
            .order_by(ItemTag.position)
        )
    )


def _kind_tags(session: Session, kind: str) -> dict[str, Tag]:
    return {
        tag.name.casefold(): tag for tag in session.scalars(select(Tag).where(Tag.tag_kind == kind))
    }


def set_item_tags(session: Session, item: Item, names: list[str]) -> list[str]:
    """整组替换，没有的标签自动建。同类型里已有不同大小写的写法时用已有的。"""
    if item.kind not in TAGGABLE_KINDS:
        raise TagError("只有作品和系列能打标签，季和集不能")
    wanted = clean_names(names)
    kind = tag_kind(item.data_type_id)
    existing = _kind_tags(session, kind)
    session.execute(
        delete(ItemTag).where(
            ItemTag.item_id == item.id,
            ItemTag.tag_id.in_(select(Tag.id).where(Tag.tag_kind == kind)),
        )
    )
    for position, name in enumerate(wanted):
        tag = existing.get(name.casefold())
        if tag is None:
            tag = Tag(tag_kind=kind, name=name)
            session.add(tag)
            session.flush()
            existing[name.casefold()] = tag
        session.add(ItemTag(item_id=item.id, tag_id=tag.id, position=position))
    item.updated_at = utc_now()
    session.commit()
    return item_tags(session, item)


def change_item_tags(session: Session, item: Item, add: list[str], remove: list[str]) -> list[str]:
    removed = {name.casefold() for name in clean_names(remove)}
    current = [name for name in item_tags(session, item) if name.casefold() not in removed]
    return set_item_tags(session, item, current + clean_names(add))


def list_tags(session: Session, data_type_id: str) -> list[dict[str, Any]]:
    if session.get(DataType, data_type_id) is None:
        raise TagError("没有这个数据类型", status_code=404)
    count = func.count(ItemTag.item_id)
    rows = session.execute(
        select(Tag, count)
        .outerjoin(ItemTag, ItemTag.tag_id == Tag.id)
        .where(Tag.tag_kind == tag_kind(data_type_id))
        .group_by(Tag.id)
        .order_by(func.lower(Tag.name), Tag.id)
    ).all()
    return [{"id": tag.id, "name": tag.name, "count": n} for tag, n in rows]


def get_tag(session: Session, tag_id: int) -> Tag:
    tag = session.get(Tag, tag_id)
    if tag is None or not tag.tag_kind.startswith(PREFIX):
        raise TagError("没有这个标签", status_code=404)
    return tag


def tagged_items(session: Session, tag_ids: list[int]) -> list[int]:
    return list(
        session.scalars(
            select(ItemTag.item_id)
            .where(ItemTag.tag_id.in_(tag_ids))
            .distinct()
            .order_by(ItemTag.item_id)
        )
    )


def _check_expected(affected: list[int], expected: int) -> None:
    if len(affected) != expected:
        raise TagError(
            f"影响的作品数变了（现在是 {len(affected)} 部），请重新确认", status_code=409
        )


def _touch(session: Session, item_ids: list[int]) -> None:
    if item_ids:
        session.execute(update(Item).where(Item.id.in_(item_ids)).values(updated_at=utc_now()))


def rename_tag(session: Session, tag: Tag, raw: str) -> list[int]:
    """返回用到这个标签的作品。改成同类型里另一个标签的名字时报错，那要用合并。"""
    name = clean_name(raw)
    other = _kind_tags(session, tag.tag_kind).get(name.casefold())
    if other is not None and other.id != tag.id:
        raise TagError(f"已经有标签「{other.name}」了，要并到一起请用合并", status_code=409)
    affected = tagged_items(session, [tag.id])
    if name != tag.name:
        tag.name = name
        _touch(session, affected)
        session.commit()
    return affected


def merge_sources(session: Session, source_ids: list[int], target: Tag) -> list[Tag]:
    sources = [get_tag(session, tag_id) for tag_id in dict.fromkeys(source_ids)]
    if not sources:
        raise TagError("要选至少一个并过去的标签")
    if any(tag.id == target.id for tag in sources):
        raise TagError("不能把标签并到它自己")
    if any(tag.tag_kind != target.tag_kind for tag in sources):
        raise TagError("只能合并同一种数据类型的标签")
    return sources


def merge_tags(session: Session, sources: list[Tag], target: Tag, expected: int) -> list[int]:
    """把 sources 并到 target 后删掉 sources。已有 target 的作品不会重复。返回受影响的作品。"""
    source_ids = [tag.id for tag in sources]
    affected = tagged_items(session, source_ids)
    _check_expected(affected, expected)
    has_target = set(tagged_items(session, [target.id]))
    rows = session.scalars(
        select(ItemTag)
        .where(ItemTag.tag_id.in_(source_ids))
        .order_by(ItemTag.item_id, ItemTag.position)
    ).all()
    moved: set[int] = set()
    for row in rows:
        if row.item_id in has_target or row.item_id in moved:
            session.delete(row)
        else:
            row.tag_id = target.id
            moved.add(row.item_id)
    session.flush()
    for tag in sources:
        session.delete(tag)
    _touch(session, affected)
    session.commit()
    return affected


def delete_tag(session: Session, tag: Tag, expected: int) -> list[int]:
    affected = tagged_items(session, [tag.id])
    _check_expected(affected, expected)
    session.execute(delete(ItemTag).where(ItemTag.tag_id == tag.id))
    session.delete(tag)
    _touch(session, affected)
    session.commit()
    return affected
