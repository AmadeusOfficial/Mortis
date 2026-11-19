"""删除单部作品：本地文件和库里的记录一起删。预览和执行用同一套计算，执行时重新算一遍。"""

import json
import os
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import delete, exists, func, or_, select
from sqlalchemy.orm import Session

from mortis.db.models import Item, ItemImage, Library, LibraryPath, MediaFile, Person, Task
from mortis.files.trash import Trash
from mortis.items.edit import ItemError
from mortis.scrape.images import ImageStore
from mortis.scrape.service import drop_empty_collections
from mortis.services import KeyedLocks
from mortis.tasks import repo as task_repo

BUSY_MESSAGE = "正在处理，稍后再删"
LINK_REASON = "文件夹里有链接，只删链接本身"
SAMPLE_SIZE = 10


@dataclass
class DeletePlan:
    item: Item
    folders: list[str]
    files: list[str]
    file_count: int = 0
    total_size: int = 0
    permanent: bool = False
    unregistered_count: int = 0
    """独占文件夹里会一起删的未登记文件。"""
    unregistered_size: int = 0
    unregistered_sample: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Expected:
    """用户在预览里确认过的内容，执行时重新算出来的必须完全一样。"""

    folders: list[str]
    files: list[str]
    file_count: int
    total_size: int
    permanent: bool
    unregistered_count: int


def _key(path: str) -> str:
    return os.path.normcase(os.path.normpath(path))


def _strictly_inside(path_key: str, root_key: str) -> bool:
    return path_key != root_key and path_key.startswith(root_key.rstrip(os.sep) + os.sep)


def _under(path_key: str, folder_key: str) -> bool:
    return path_key == folder_key or _strictly_inside(path_key, folder_key)


def _resolve(path: str) -> str:
    return _key(str(Path(path).resolve()))


def _resolved_key(path: str) -> str:
    try:
        return _resolve(path)
    except OSError, RuntimeError, ValueError:
        raise ItemError(f"这个路径无法确认位置，不删：{path}", status_code=400) from None


def _is_link(path: str) -> bool:
    return os.path.islink(path) or os.path.isjunction(path)


class _Resolver:
    """把路径换成 resolve 后的样子：库目录各 resolve 一次，库目录下的路径只替换前缀。"""

    def __init__(self, roots: Iterable[str]) -> None:
        pairs = []
        for root in roots:
            try:
                resolved = _resolve(root)
            except OSError, RuntimeError, ValueError:
                resolved = _key(root)
            pairs.append((_key(root), resolved))
        self.roots = sorted(pairs, key=lambda pair: len(pair[0]), reverse=True)

    def key(self, path: str) -> str:
        literal = _key(path)
        for root, resolved in self.roots:
            if _under(literal, root):
                rest = literal[len(root) :].lstrip(os.sep)
                return os.path.join(resolved, rest) if rest else resolved
        try:
            return _resolve(path)
        except OSError, RuntimeError, ValueError:
            return literal


@dataclass
class _Scan:
    files: list[tuple[str, int]] = field(default_factory=list)
    """（路径，大小）。链接只算链接本身。"""
    has_links: bool = False
    failed: bool = False
    """有目录读不了，看不全里面有什么。"""


def _scan(folder: str, scan: _Scan) -> None:
    """自己递归，不进联接和符号链接。"""
    try:
        with os.scandir(folder) as scanned:
            entries = list(scanned)
    except OSError:
        scan.failed = True
        return
    for entry in entries:
        try:
            is_link = entry.is_junction() or entry.is_symlink()
            is_dir = not is_link and entry.is_dir(follow_symlinks=False)
        except OSError:
            scan.failed = True
            continue
        if is_dir:
            _scan(entry.path, scan)
            continue
        scan.has_links = scan.has_links or is_link
        scan.files.append((entry.path, _lsize(entry.path)))


def _lsize(path: str) -> int:
    try:
        return os.lstat(path).st_size
    except OSError:
        return 0


def check_deletable(session: Session, item: Item) -> None:
    if item.kind == "collection":
        raise ItemError("系列不能直接删，删掉其中的作品后系列会一起去掉", status_code=400)
    if item.kind in ("season", "episode"):
        raise ItemError("季和集不能单独删", status_code=400)
    if item.kind != "work":
        raise ItemError("只有作品能删", status_code=400)


def _root_of(path: str, roots: list[str]) -> str | None:
    path_key = _key(path)
    for root in roots:
        if _strictly_inside(path_key, _key(root)):
            return root
    return None


def _candidate(rows: list[MediaFile]) -> str | None:
    """独占文件夹只可能是唯一的游戏文件夹，或唯一的视频所在目录；条目的文件都得在它里面。"""
    games = {row.path for row in rows if row.file_kind == "game"}
    if games:
        folder = games.pop() if len(games) == 1 else None
    else:
        folders = {os.path.dirname(row.path) for row in rows if row.file_kind == "video"}
        folder = folders.pop() if len(folders) == 1 else None
    if folder is None:
        return None
    folder_key = _key(folder)
    if not all(_under(_key(row.path), folder_key) for row in rows):
        return None
    return folder


def _tree_ids(session: Session, item: Item) -> set[int]:
    """作品自己，加上季和集。删剧时这些文件都算这部剧的。"""
    ids = {item.id}
    children = set(session.scalars(select(Item.id).where(Item.parent_id == item.id)))
    ids |= children
    if children:
        ids |= set(session.scalars(select(Item.id).where(Item.parent_id.in_(children))))
    return ids


def _has_foreign(session: Session, owned: set[int], folder: str, resolver: _Resolver) -> bool:
    """文件夹里（递归，按 resolve 后的路径）有别的条目或没认出的登记文件。"""
    folder_key = resolver.key(folder)
    for item_id, path in session.execute(select(MediaFile.item_id, MediaFile.path)):
        if item_id not in owned and _under(resolver.key(path), folder_key):
            return True
    return False


def _contains_root(folder: str, resolver: _Resolver) -> bool:
    folder_key = _resolved_key(folder)
    return any(_under(resolved, folder_key) for _literal, resolved in resolver.roots)


def _unregistered(scan: _Scan, rows: list[MediaFile]) -> list[tuple[str, int]]:
    registered = [_key(row.path) for row in rows]
    return [
        (path, size)
        for path, size in scan.files
        if not any(_under(_key(path), known) for known in registered)
    ]


def _check_safe(
    session: Session,
    owned: set[int],
    targets: tuple[list[str], list[str]],
    roots: list[str],
    resolver: _Resolver,
) -> None:
    folders, files = targets
    resolved_roots = [_resolved_key(root) for root in roots]
    for target in [*folders, *files]:
        resolved = _resolved_key(target)
        if not any(_strictly_inside(resolved, root) for root in resolved_roots):
            raise ItemError(
                f"这个路径不在库目录里，或者就是库目录本身，不删：{target}", status_code=400
            )
    for folder in folders:
        if _contains_root(folder, resolver):
            raise ItemError(f"这个文件夹里有库目录，不删：{folder}", status_code=400)
        if _has_foreign(session, owned, folder, resolver):
            raise ItemError(f"这个文件夹里还有别的条目的文件，不删：{folder}", status_code=400)


def plan_delete(
    session: Session, item: Item, trash: Trash, video_extensions: frozenset[str]
) -> DeletePlan:
    check_deletable(session, item)
    owned = _tree_ids(session, item)
    library = session.get(Library, item.library_id)
    roots = [row.path for row in library.paths] if library is not None else []
    resolver = _Resolver(session.scalars(select(LibraryPath.path)))
    rows = list(
        session.scalars(
            select(MediaFile).where(MediaFile.item_id.in_(owned)).order_by(MediaFile.path)
        )
    )
    for row in rows:
        root = _root_of(row.path, roots)
        if root is None:
            raise ItemError(
                f"这个文件不在库目录里，或者就是库目录本身，不删：{row.path}", status_code=400
            )
        if not os.path.isdir(root):
            raise ItemError(f"库目录现在访问不到，稍后再删：{root}", status_code=409)

    plan = DeletePlan(item, [], [])
    candidate = _candidate(rows)
    exclusive: _Scan | None = None
    if (
        candidate is not None
        and not _is_link(candidate)
        and _root_of(candidate, roots) is not None
        and _resolved_key(candidate) == resolver.key(candidate)
        and not _contains_root(candidate, resolver)
        and not _has_foreign(session, owned, candidate, resolver)
    ):
        scan = _Scan()
        _scan(candidate, scan)
        extra = _unregistered(scan, rows)
        if not scan.failed and not any(
            os.path.splitext(path)[1].lower() in video_extensions for path, _ in extra
        ):
            exclusive = scan
            plan.folders = [candidate]
            plan.unregistered_count = len(extra)
            plan.unregistered_size = sum(size for _, size in extra)
            plan.unregistered_sample = sorted(
                os.path.relpath(path, candidate) for path, _ in extra
            )[:SAMPLE_SIZE]
            plan.reasons.append(
                f"「{candidate}」里只有这部作品的文件，整个文件夹一起删，包括没登记的文件"
            )
    if exclusive is None:
        plan.folders = sorted({row.path for row in rows if row.file_kind == "game"})
        folder_keys = [_key(folder) for folder in plan.folders]
        plan.files = sorted(
            row.path
            for row in rows
            if row.file_kind != "game" and not any(_under(_key(row.path), f) for f in folder_keys)
        )
        if rows:
            plan.reasons.append("条目的文件不在一个只属于它的文件夹里，只删登记的文件")
    _check_safe(session, owned, (plan.folders, plan.files), roots, resolver)
    _measure(plan, exclusive)
    plan.permanent = any(
        trash.is_permanent(Path(target)) for target in [*plan.folders, *plan.files]
    )
    return plan


def _measure(plan: DeletePlan, exclusive: _Scan | None) -> None:
    missing = 0
    has_links = False
    for folder in plan.folders:
        if not os.path.lexists(folder):
            missing += 1
            continue
        if exclusive is not None:
            scan = exclusive
        else:
            scan = _Scan()
            _scan(folder, scan)
        has_links = has_links or scan.has_links
        plan.file_count += len(scan.files)
        plan.total_size += sum(size for _, size in scan.files)
    for path in plan.files:
        if not os.path.lexists(path):
            missing += 1
            continue
        plan.file_count += 1
        plan.total_size += _lsize(path)
    if has_links:
        plan.reasons.append(LINK_REASON)
    if missing:
        plan.reasons.append(f"有 {missing} 个登记的文件已经不在磁盘上，只从库里去掉")


def _active_tasks(session: Session, item: Item) -> list[Task]:
    return list(
        session.scalars(
            select(Task).where(
                Task.status.in_(task_repo.ACTIVE),
                or_(
                    func.json_extract(Task.payload, "$.item_id") == item.id,
                    func.json_extract(Task.payload, "$.library_id") == item.library_id,
                ),
            )
        )
    )


def _check_tasks(session: Session, item: Item) -> list[int]:
    """库级任务排队或在跑、这个条目的任务在跑时拒绝。返回这个条目排队中的任务。"""
    queued = []
    for task in _active_tasks(session, item):
        payload = json.loads(task.payload or "{}")
        if payload.get("library_id") == item.library_id or task.status == task_repo.RUNNING:
            raise ItemError(BUSY_MESSAGE, status_code=409)
        queued.append(task.id)
    return queued


def _prune_empty(start: Path, root: str) -> None:
    """从 start 往上删空目录，到库目录为止（不含库目录），遇到链接就停。"""
    root_key = _key(root)
    current = start
    while _strictly_inside(_key(str(current)), root_key) and not _is_link(str(current)):
        try:
            current.rmdir()
        except OSError:
            return
        current = current.parent


def _images_of(session: Session, item: Item) -> set[str]:
    rels = set(
        session.scalars(
            select(ItemImage.file_path).where(
                ItemImage.item_id == item.id, ItemImage.file_path.is_not(None)
            )
        )
    )
    if item.poster_path:
        rels.add(item.poster_path)
    return rels


def _drop_orphan_images(session: Session, data_dir: Path, rels: set[str]) -> None:
    """图片按内容哈希存，多部作品可能共用一份；没人再用的才删。"""
    store = ImageStore(data_dir)
    for rel in rels:
        used = session.scalar(
            select(
                exists().where(Item.poster_path == rel)
                | exists().where(ItemImage.file_path == rel)
                | exists().where(Person.photo_path == rel)
            )
        )
        path = None if used else store.resolve(rel)
        if path is not None:
            try:
                path.unlink()
            except OSError:
                pass


def _matches(plan: DeletePlan, expected: Expected) -> bool:
    return (
        sorted(expected.folders) == plan.folders
        and sorted(expected.files) == plan.files
        and expected.file_count == plan.file_count
        and expected.total_size == plan.total_size
        and expected.permanent == plan.permanent
        and expected.unregistered_count == plan.unregistered_count
    )


def delete_item(
    session: Session,
    item: Item,
    trash: Trash,
    expected: Expected,
    *,
    video_extensions: frozenset[str],
    data_dir: Path,
    locks: KeyedLocks,
) -> int:
    """先删磁盘再删库。返回所在库的编号。部分文件没删掉时库里只去掉已删的记录，条目保留。

    整段持有和整理同一把库锁；拿锁前先查一遍任务，免得整理在跑时请求一直等着。
    """
    check_deletable(session, item)
    _check_tasks(session, item)
    library_id = item.library_id
    with locks.hold(f"library:{library_id}"):
        queued = _check_tasks(session, item)
        plan = plan_delete(session, item, trash, video_extensions)
        if not _matches(plan, expected):
            raise ItemError("要删的文件变了，请重新确认", status_code=409)
        # 删盘前取消，排队中的输出任务才不会在删完后把 NFO 和图片写回去；删失败时多取消了也无妨。
        if queued:
            _cancelled, running = task_repo.cancel(session, queued)
            if running:
                raise ItemError(BUSY_MESSAGE, status_code=409)

        library = session.get(Library, library_id)
        roots = [row.path for row in library.paths] if library is not None else []
        removed: list[str] = []
        failed: list[tuple[str, str]] = []
        for target in [*plan.folders, *plan.files]:
            path = Path(target)
            if os.path.lexists(path):
                try:
                    trash.remove(path)
                except OSError as error:
                    failed.append((target, str(error)))
                    continue
            removed.append(target)
        for target in plan.files:
            root = _root_of(target, roots)
            if target in removed and root is not None:
                _prune_empty(Path(target).parent, root)

        removed_keys = [_key(target) for target in removed]
        owned = _tree_ids(session, item)
        with locks.hold("catalog-write"):
            rows = list(session.scalars(select(MediaFile).where(MediaFile.item_id.in_(owned))))
            if failed:
                for row in rows:
                    if any(_under(_key(row.path), key) for key in removed_keys):
                        session.delete(row)
                session.commit()
                listed = "；".join(f"{path}（{error}）" for path, error in failed)
                raise ItemError(
                    f"有 {len(failed)} 个没删掉：{listed}。已删掉的已从库里去掉，可以再删一次",
                    status_code=409,
                )
            images: set[str] = set()
            for owned_id in owned:
                owned_item = session.get(Item, owned_id)
                if owned_item is not None:
                    images |= _images_of(session, owned_item)
            parent = session.get(Item, item.parent_id) if item.parent_id is not None else None
            if parent is not None and parent.kind == "collection":
                others = session.scalar(
                    select(exists().where(Item.parent_id == parent.id, Item.id != item.id))
                )
                if not others:
                    images |= _images_of(session, parent)
            session.execute(delete(MediaFile).where(MediaFile.item_id.in_(owned)))
            session.execute(delete(Item).where(Item.id == item.id))
            drop_empty_collections(session, library_id)
            session.commit()
            _drop_orphan_images(session, data_dir, images)
    return library_id
