from dataclasses import dataclass

from sqlalchemy import ColumnElement, exists, select
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


def manual_clause() -> ColumnElement[bool]:
    """Item 是手动条目：没有任何外部编号的作品。"""
    return (Item.kind == "work") & ~exists().where(ItemExternalId.item_id == Item.id)


def manual_ids(session: Session, items: list[Item]) -> set[int]:
    """这些条目里的手动条目。"""
    works = [item.id for item in items if item.kind == "work"]
    if not works:
        return set()
    return set(session.scalars(select(Item.id).where(Item.id.in_(works), manual_clause())))


def is_manual(session: Session, item: Item) -> bool:
    """季和集跟着所属的剧算。"""
    work = session.get(Item, work_of(session, item.id))
    return work is not None and bool(manual_ids(session, [work]))


def create_manual_work(session: Session, library: Library, title: str) -> Item:
    """不写外部编号。只 flush，调用方写字段并提交。"""
    now = utc_now()
    item = Item(
        library_id=library.id,
        data_type_id=library.data_type_id,
        kind="work",
        title=title,
        fields="{}",
        field_sources="{}",
        locked_fields="[]",
        created_at=now,
        updated_at=now,
    )
    session.add(item)
    session.flush()
    return item


@dataclass(frozen=True)
class ExternalRef:
    """插件类型的条目身份：来源插件的外部编号。"""

    provider: str
    external_id: str
    title: str
    original_title: str | None = None
    release_date: str | None = None


def find_by_external(
    session: Session, library_id: int, provider: str, external_id: str
) -> Item | None:
    """只认主编号。补充来源的编号可能是同系列的另一作（同一 VNDB 条目下的不同版本），不拿来去重。"""
    return session.scalar(
        select(Item)
        .join(ItemExternalId, ItemExternalId.item_id == Item.id)
        .where(
            Item.library_id == library_id,
            Item.kind == "work",
            ItemExternalId.provider == provider,
            ItemExternalId.external_id == external_id,
            ItemExternalId.is_primary == 1,
        )
        .order_by(Item.id)
        .limit(1)
    )


def find_or_create_work(session: Session, library: Library, ref: ExternalRef) -> tuple[Item, bool]:
    """同一个库里按外部编号去重。只 flush，调用方提交。"""
    existing = find_by_external(session, library.id, ref.provider, ref.external_id)
    if existing is not None:
        return existing, False
    now = utc_now()
    item = Item(
        library_id=library.id,
        data_type_id=library.data_type_id,
        kind="work",
        title=ref.title or f"{ref.provider} {ref.external_id}",
        original_title=ref.original_title or None,
        release_date=ref.release_date or None,
        created_at=now,
        updated_at=now,
    )
    session.add(item)
    session.flush()
    session.add(
        ItemExternalId(
            item_id=item.id, provider=ref.provider, external_id=ref.external_id, is_primary=1
        )
    )
    refresh_subtitle(session, item)
    session.flush()
    return item, True


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
