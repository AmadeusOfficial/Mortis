"""剧下面的季和集：建行，视频和字幕改挂到集上。刮削和手动建条目共用。"""

import os

from sqlalchemy import select
from sqlalchemy.orm import Session

from mortis.db.models import Item, MediaFile, utc_now
from mortis.organize.names import subtitle_belongs_to


def child(session: Session, parent: Item, kind: str, number: int) -> Item:
    """取或建剧下的季、季下的集。新建的标题默认「第 N 季」「第 N 集」。"""
    row = session.scalar(
        select(Item).where(
            Item.parent_id == parent.id, Item.kind == kind, Item.index_number == number
        )
    )
    if row is None:
        now = utc_now()
        row = Item(
            library_id=parent.library_id,
            data_type_id=parent.data_type_id,
            parent_id=parent.id,
            kind=kind,
            index_number=number,
            title=f"第 {number} 季" if kind == "season" else f"第 {number} 集",
            created_at=now,
            updated_at=now,
        )
        session.add(row)
        session.flush()
    return row


def bind_episodes(
    session: Session, files: list[MediaFile], targets: dict[str, int], show_id: int
) -> int:
    """视频按季集号改挂到集，字幕跟同名视频走；对不上的留在剧上。返回挂上的视频数。"""
    bound = 0
    videos = {row.path: row for row in files if row.file_kind == "video"}
    for path, row in videos.items():
        target = targets.get(path, show_id)
        bound += target != show_id
        if row.item_id != target:
            row.item_id = target
            row.updated_at = utc_now()
    stems = {os.path.splitext(path)[0]: row.item_id for path, row in videos.items()}
    for row in files:
        if row.file_kind != "subtitle":
            continue
        folder, name = os.path.split(row.path)
        owner = next(
            (
                item_id
                for stem, item_id in stems.items()
                if os.path.dirname(stem) == folder
                and subtitle_belongs_to(name, os.path.basename(stem))
            ),
            show_id,
        )
        if row.item_id != owner:
            row.item_id = owner
            row.updated_at = utc_now()
    return bound
