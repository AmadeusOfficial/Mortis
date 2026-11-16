from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from mortis.db.models import Item, ItemExternalId, Library, utc_now
from mortis.items.subtitle import refresh_subtitle


@dataclass(frozen=True)
class MovieRef:
    tmdb_id: int
    title: str
    original_title: str | None = None
    release_date: str | None = None


def find_by_tmdb(
    session: Session, library_id: int, tmdb_id: int, kind: str = "work"
) -> Item | None:
    return session.scalar(
        select(Item)
        .join(ItemExternalId, ItemExternalId.item_id == Item.id)
        .where(
            Item.library_id == library_id,
            Item.kind == kind,
            ItemExternalId.provider == "tmdb",
            ItemExternalId.external_id == str(tmdb_id),
        )
        .order_by(Item.id)
        .limit(1)
    )


def work_of(session: Session, item_id: int) -> int:
    """季和集向上找到所属的剧；作品和系列原样返回。"""
    item = session.get(Item, item_id)
    while item is not None and item.kind in ("season", "episode") and item.parent_id is not None:
        item = session.get(Item, item.parent_id)
    return item.id if item is not None else item_id


def find_or_create_movie(session: Session, library: Library, ref: MovieRef) -> tuple[Item, bool]:
    """同一个库里按 TMDB 编号去重。只 flush，调用方提交。"""
    existing = find_by_tmdb(session, library.id, ref.tmdb_id)
    if existing is not None:
        return existing, False
    now = utc_now()
    item = Item(
        library_id=library.id,
        data_type_id=library.data_type_id,
        kind="work",
        title=ref.title or f"TMDB {ref.tmdb_id}",
        original_title=ref.original_title or None,
        release_date=ref.release_date or None,
        created_at=now,
        updated_at=now,
    )
    session.add(item)
    session.flush()
    session.add(
        ItemExternalId(item_id=item.id, provider="tmdb", external_id=str(ref.tmdb_id), is_primary=1)
    )
    refresh_subtitle(session, item)
    session.flush()
    return item, True
