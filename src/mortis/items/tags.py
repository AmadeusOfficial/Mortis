"""标签的唯一写入口：刮削、用户手动、插件钩子（和智能体工具）都从这里写。

表：item_tags 是各来源标签的并集，展示、筛选和 NFO 都只读它；
item_tag_origins 记每个标签由谁加的（scrape、插件 id、manual），
manual_removed 是用户删除过这个标签的记号，之后刮削和插件都不再补回。

规则：
- 刮削只替换自己名下（scrape）的标签；插件只能增删自己 id 名下的标签；
- 撤掉一个来源后，没有任何来源了才从 item_tags 删掉，两步在同一事务里；
- 字段被锁定时，刮削和插件都跳过；
- position 的归属：刮削的标签排在前面，按刮削给的顺序；其余来源的标签跟在后面，
  保持原有的相对顺序，所以刮削重排不会打乱插件标签之间的顺序；用户手动保存时，整组按用户给的顺序。

加锁顺序固定为 catalog-write，再 item:{id}。刮削流水线已经持有 catalog-write 来写库，
所以 replace_scrape_tags 不再取锁；手动、钩子和智能体走 set_item_tags、add_tags、remove_tags
这些入口，它们按上面的顺序取两把锁，并在锁内提交。需要事务提交之后才重写 NFO。
"""

import json
from collections.abc import Callable, Iterable
from contextlib import ExitStack
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from sqlalchemy import delete, exists, func, select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session

from mortis.db.models import Item, ItemTag, ItemTagOrigin, Tag, TypeField

if TYPE_CHECKING:
    from mortis.services import KeyedLocks
    from mortis.tasks.runner import TaskRunner

SCRAPE = "scrape"
MANUAL = "manual"
MANUAL_REMOVED = "manual_removed"
RESERVED_ORIGINS = frozenset({SCRAPE, MANUAL, MANUAL_REMOVED})
CATALOG_LOCK = "catalog-write"


def item_lock_key(item_id: int) -> str:
    return f"item:{item_id}"


@dataclass(frozen=True)
class TagWriteResult:
    """一次写入实际发生了什么。被忽略的分三种原因，调用方可以区分。"""

    applied: tuple[str, ...] = ()  # 实际新增（增）或撤掉（删）的标签
    unchanged: tuple[str, ...] = ()  # 增：这个来源早就加过；删：本来就没有
    locked: tuple[str, ...] = ()  # 字段被锁定，整批跳过
    removed_by_user: tuple[str, ...] = ()  # 用户手动删除过，增被忽略
    not_owned: tuple[str, ...] = ()  # 删：不是这个来源加的，不能删

    @property
    def changed(self) -> bool:
        return bool(self.applied)


@dataclass
class _Draft:
    applied: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    locked: list[str] = field(default_factory=list)
    removed_by_user: list[str] = field(default_factory=list)
    not_owned: list[str] = field(default_factory=list)

    def result(self) -> TagWriteResult:
        return TagWriteResult(
            tuple(self.applied),
            tuple(self.unchanged),
            tuple(self.locked),
            tuple(self.removed_by_user),
            tuple(self.not_owned),
        )


def clean_names(names: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(name.strip() for name in names if name and name.strip()))


def _field_key(session: Session, item: Item, tag_kind: str) -> str | None:
    return session.scalar(
        select(TypeField.field_key).where(
            TypeField.data_type_id == item.data_type_id,
            TypeField.applies_to == item.kind,
            TypeField.stored_in == "tags",
            func.coalesce(TypeField.tag_kind, TypeField.field_key) == tag_kind,
        )
    )


def is_locked(session: Session, item: Item, tag_kind: str) -> bool:
    key = _field_key(session, item, tag_kind)
    return key is not None and key in json.loads(item.locked_fields)


def _tag_id(session: Session, tag_kind: str, name: str) -> int:
    session.execute(insert(Tag).values(tag_kind=tag_kind, name=name).on_conflict_do_nothing())
    tag_id = session.scalar(select(Tag.id).where(Tag.tag_kind == tag_kind, Tag.name == name))
    if tag_id is None:  # 刚 UPSERT 过，找不到只可能是库坏了
        raise RuntimeError(f"标签写入失败：{tag_kind}/{name}")
    return tag_id


def _has_origin(session: Session, item_id: int, tag_id: int, origin: str) -> bool:
    return (
        session.scalar(
            select(
                exists().where(
                    ItemTagOrigin.item_id == item_id,
                    ItemTagOrigin.tag_id == tag_id,
                    ItemTagOrigin.origin == origin,
                )
            )
        )
        is True
    )


def _add_origin(session: Session, item_id: int, tag_id: int, origin: str) -> None:
    session.execute(
        insert(ItemTagOrigin)
        .values(item_id=item_id, tag_id=tag_id, origin=origin)
        .on_conflict_do_nothing()
    )


def _remove_origin(session: Session, item_id: int, tag_id: int, origin: str) -> None:
    session.execute(
        delete(ItemTagOrigin).where(
            ItemTagOrigin.item_id == item_id,
            ItemTagOrigin.tag_id == tag_id,
            ItemTagOrigin.origin == origin,
        )
    )


def _ensure_shown(session: Session, item_id: int, tag_id: int) -> None:
    session.execute(
        insert(ItemTag)
        .values(item_id=item_id, tag_id=tag_id, position=1_000_000)
        .on_conflict_do_nothing()
    )


def _drop_unowned(session: Session, item_id: int, tag_ids: Iterable[int]) -> None:
    """没有任何来源（manual_removed 不算）的标签，才从展示表里删掉。"""
    ids = list(tag_ids)
    if not ids:
        return
    owner = (
        select(ItemTagOrigin.tag_id)
        .where(
            ItemTagOrigin.item_id == ItemTag.item_id,
            ItemTagOrigin.tag_id == ItemTag.tag_id,
            ItemTagOrigin.origin != MANUAL_REMOVED,
        )
        .exists()
    )
    session.execute(
        delete(ItemTag).where(ItemTag.item_id == item_id, ItemTag.tag_id.in_(ids), ~owner)
    )


def _renumber(session: Session, item_id: int, tag_kind: str, lead: list[int]) -> None:
    """lead 里的标签排最前，其余保持原有顺序跟在后面。"""
    rows = session.execute(
        select(ItemTag.tag_id, ItemTag.position)
        .join(Tag, Tag.id == ItemTag.tag_id)
        .where(ItemTag.item_id == item_id, Tag.tag_kind == tag_kind)
        .order_by(ItemTag.position, ItemTag.tag_id)
    ).all()
    present = {row.tag_id for row in rows}
    ordered = [tag_id for tag_id in dict.fromkeys(lead) if tag_id in present]
    ordered += [row.tag_id for row in rows if row.tag_id not in ordered]
    positions = {row.tag_id: row.position for row in rows}
    for index, tag_id in enumerate(ordered):
        if positions[tag_id] != index:
            session.execute(
                ItemTag.__table__.update()
                .where(ItemTag.item_id == item_id, ItemTag.tag_id == tag_id)
                .values(position=index)
            )


def _scrape_tag_ids(session: Session, item_id: int, tag_kind: str) -> set[int]:
    return set(
        session.scalars(
            select(ItemTagOrigin.tag_id)
            .join(Tag, Tag.id == ItemTagOrigin.tag_id)
            .where(
                ItemTagOrigin.item_id == item_id,
                ItemTagOrigin.origin == SCRAPE,
                Tag.tag_kind == tag_kind,
            )
        )
    )


def replace_scrape_tags(
    session: Session, item: Item, tag_kind: str, names: Iterable[str]
) -> TagWriteResult:
    """刮削：只替换 scrape 来源，不碰插件和手动的。调用方已持有 catalog-write。"""
    wanted = clean_names(names)
    draft = _Draft()
    if is_locked(session, item, tag_kind):
        draft.locked = wanted
        return draft.result()
    keep: list[int] = []
    for name in wanted:
        tag_id = _tag_id(session, tag_kind, name)
        if _has_origin(session, item.id, tag_id, MANUAL_REMOVED):
            draft.removed_by_user.append(name)
            continue
        keep.append(tag_id)
        if _has_origin(session, item.id, tag_id, SCRAPE):
            draft.unchanged.append(name)
        else:
            draft.applied.append(name)
        _add_origin(session, item.id, tag_id, SCRAPE)
        _ensure_shown(session, item.id, tag_id)
    stale = _scrape_tag_ids(session, item.id, tag_kind) - set(keep)
    for tag_id in stale:
        _remove_origin(session, item.id, tag_id, SCRAPE)
    _drop_unowned(session, item.id, stale)
    _renumber(session, item.id, tag_kind, keep)
    return draft.result()


def set_manual_tags(
    session: Session, item: Item, tag_kind: str, names: Iterable[str]
) -> TagWriteResult:
    """用户手动保存整组标签。新增的记 manual；去掉的清掉所有来源并记 manual_removed。

    调用方已持有 catalog-write 和 item 锁（见 set_item_tags）。用户可以改锁定的字段。
    """
    wanted = clean_names(names)
    draft = _Draft()
    current = {
        row.tag_id: row.name
        for row in session.execute(
            select(ItemTag.tag_id, Tag.name)
            .join(Tag, Tag.id == ItemTag.tag_id)
            .where(ItemTag.item_id == item.id, Tag.tag_kind == tag_kind)
        )
    }
    ordered: list[int] = []
    for name in wanted:
        tag_id = _tag_id(session, tag_kind, name)
        ordered.append(tag_id)
        _remove_origin(session, item.id, tag_id, MANUAL_REMOVED)
        if tag_id in current:
            draft.unchanged.append(name)
            continue
        _add_origin(session, item.id, tag_id, MANUAL)
        _ensure_shown(session, item.id, tag_id)
        draft.applied.append(name)
    for tag_id in set(current) - set(ordered):
        session.execute(
            delete(ItemTagOrigin).where(
                ItemTagOrigin.item_id == item.id, ItemTagOrigin.tag_id == tag_id
            )
        )
        _add_origin(session, item.id, tag_id, MANUAL_REMOVED)
        session.execute(delete(ItemTag).where(ItemTag.item_id == item.id, ItemTag.tag_id == tag_id))
    _renumber(session, item.id, tag_kind, ordered)
    return draft.result()


def _check_origin(origin: str) -> None:
    if not origin or origin != origin.strip():
        raise ValueError("标签来源不能为空")
    if origin in RESERVED_ORIGINS:
        raise ValueError(f"「{origin}」是宿主保留的来源，插件要用自己的 id")


def _add(
    session: Session, item: Item, tag_kind: str, names: Iterable[str], origin: str
) -> TagWriteResult:
    wanted = clean_names(names)
    draft = _Draft()
    if is_locked(session, item, tag_kind):
        draft.locked = wanted
        return draft.result()
    for name in wanted:
        tag_id = _tag_id(session, tag_kind, name)
        if _has_origin(session, item.id, tag_id, MANUAL_REMOVED):
            draft.removed_by_user.append(name)
        elif _has_origin(session, item.id, tag_id, origin):
            draft.unchanged.append(name)
        else:
            _add_origin(session, item.id, tag_id, origin)
            _ensure_shown(session, item.id, tag_id)
            draft.applied.append(name)
    _renumber(session, item.id, tag_kind, [])
    return draft.result()


def _remove(
    session: Session, item: Item, tag_kind: str, names: Iterable[str], origin: str
) -> TagWriteResult:
    wanted = clean_names(names)
    draft = _Draft()
    if is_locked(session, item, tag_kind):
        draft.locked = wanted
        return draft.result()
    dropped: list[int] = []
    for name in wanted:
        tag_id = session.scalar(select(Tag.id).where(Tag.tag_kind == tag_kind, Tag.name == name))
        if tag_id is None or not _has_origin(session, item.id, tag_id, origin):
            draft.not_owned.append(name)
            continue
        _remove_origin(session, item.id, tag_id, origin)
        dropped.append(tag_id)
        draft.applied.append(name)
    _drop_unowned(session, item.id, dropped)
    _renumber(session, item.id, tag_kind, [])
    return draft.result()


def _rewrite(session: Session, runner: TaskRunner | None, item: Item, quiet: bool) -> None:
    if runner is None:
        return
    from mortis.output.service import rewrite_if_arranged

    if quiet:
        rewrite_if_arranged(session, runner, item, silent=True)
    else:
        rewrite_if_arranged(session, runner, item)


def _hold(locks: KeyedLocks, item_id: int) -> ExitStack:
    stack = ExitStack()
    stack.enter_context(locks.hold(CATALOG_LOCK))
    stack.enter_context(locks.hold(item_lock_key(item_id)))
    return stack


def _locked_write(
    session: Session,
    locks: KeyedLocks,
    runner: TaskRunner | None,
    item_id: int,
    write: Callable[[Item], TagWriteResult],
    quiet: bool = False,
) -> TagWriteResult:
    with _hold(locks, item_id):
        item = session.get(Item, item_id)
        if item is None:
            raise LookupError(f"条目 {item_id} 不存在")
        try:
            result = write(item)
            session.commit()
        except BaseException:
            session.rollback()
            raise
    if result.changed:
        _rewrite(session, runner, item, quiet)
    return result


def add_tags(
    session: Session,
    locks: KeyedLocks,
    item_id: int,
    tag_kind: str,
    names: Iterable[str],
    origin: str,
    runner: TaskRunner | None = None,
    quiet: bool = False,
) -> TagWriteResult:
    """插件按来源加标签。锁定字段、用户删除过的标签不会写入，结果里分别列出。

    quiet 为真时，写后重写 NFO 的整理任务不再发布 arrange.finished（钩子里写标签用，防止循环）。
    """
    _check_origin(origin)
    return _locked_write(
        session,
        locks,
        runner,
        item_id,
        lambda item: _add(session, item, tag_kind, names, origin),
        quiet,
    )


def remove_tags(
    session: Session,
    locks: KeyedLocks,
    item_id: int,
    tag_kind: str,
    names: Iterable[str],
    origin: str,
    runner: TaskRunner | None = None,
    quiet: bool = False,
) -> TagWriteResult:
    """插件按来源撤标签，只能撤自己名下的。quiet 同 add_tags。"""
    _check_origin(origin)
    return _locked_write(
        session,
        locks,
        runner,
        item_id,
        lambda item: _remove(session, item, tag_kind, names, origin),
        quiet,
    )


def set_item_tags(
    session: Session,
    locks: KeyedLocks,
    item_id: int,
    tag_kind: str,
    names: Iterable[str],
    runner: TaskRunner | None = None,
) -> TagWriteResult:
    """只改标签的手动保存入口。字段和标签一起改时用 items.edit.edit_fields。"""
    return _locked_write(
        session,
        locks,
        runner,
        item_id,
        lambda item: set_manual_tags(session, item, tag_kind, names),
    )


def hold_item(locks: KeyedLocks, item_id: int) -> ExitStack:
    """按固定顺序（catalog-write，再 item:{id}）取锁，给需要把标签和别的字段放同一事务的调用方。"""
    return _hold(locks, item_id)
