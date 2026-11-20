"""合集封面。方式记在 items.fields 的 cover_mode，上传的图不因换方式而删除。"""

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from mortis.db.models import Item, ItemImage

COVER_MODES = ("auto", "single", "grid")
COVER_KINDS = ("cover_1", "cover_2", "cover_3", "cover_4")


def collage_cells(posters: list[str | None]) -> list[str | None]:
    """始终四个格子。没有海报时四格都空；1 张铺满；2 张按 1、2、1、2；3 张第四格重复第一张。"""
    filled = [url for url in posters if url]
    if not filled:
        return [None, None, None, None]
    if len(filled) == 1:
        return [filled[0], filled[0], filled[0], filled[0]]
    if len(filled) == 2:
        first, second = filled
        return [first, second, first, second]
    if len(filled) == 3:
        first, second, third = filled
        return [first, second, third, first]
    return filled[:4]


def image_url(rel: str | None) -> str | None:
    return f"/api/images/{rel}" if rel else None


def cover_mode_of(item: Item) -> str | None:
    try:
        raw = json.loads(item.fields or "{}")
    except json.JSONDecodeError:
        return None
    mode = raw.get("cover_mode") if isinstance(raw, dict) else None
    return mode if mode in COVER_MODES else None


def member_order():
    """合集成员按发售日从早到晚。没有发售日的排在后面。"""
    return (
        Item.release_date.is_(None),
        Item.release_date,
        Item.id,
    )


def covers_for(session: Session, items: list[Item]) -> dict[int, dict[str, Any]]:
    """只给设过封面方式的合集填 cover_mode 和四格地址。电影系列没有这个字段，继续用单张海报。"""
    collections = [item for item in items if item.kind == "collection" and cover_mode_of(item)]
    if not collections:
        return {}
    ids = [item.id for item in collections]
    children = list(
        session.scalars(select(Item).where(Item.parent_id.in_(ids)).order_by(*member_order()))
    )
    by_parent: dict[int, list[str | None]] = {item_id: [] for item_id in ids}
    for child in children:
        if child.parent_id is not None:
            by_parent[child.parent_id].append(image_url(child.poster_path))
    custom: dict[int, dict[str, str | None]] = {item_id: {} for item_id in ids}
    rows = session.scalars(
        select(ItemImage).where(
            ItemImage.item_id.in_(ids),
            ItemImage.image_kind.in_(COVER_KINDS),
            ItemImage.file_path.is_not(None),
        )
    )
    for row in rows:
        custom[row.item_id][row.image_kind] = image_url(row.file_path)
    found: dict[int, dict[str, Any]] = {}
    for item in collections:
        mode = cover_mode_of(item)
        auto = collage_cells(by_parent[item.id])
        if mode == "grid":
            urls = [
                custom[item.id].get(kind) or auto[index] for index, kind in enumerate(COVER_KINDS)
            ]
        elif mode == "auto":
            urls = auto
        else:
            urls = []
        found[item.id] = {"cover_mode": mode, "cover_urls": urls}
    return found
