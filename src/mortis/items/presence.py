"""作品有没有视频或游戏目录。海报墙和扫描清理都用这一条。"""

from sqlalchemy import delete, exists, or_, select
from sqlalchemy.orm import Session, aliased

from mortis.db.models import Item, MediaFile

MAIN_KINDS = ("video", "game")


def work_has_main_file():
    """关联到当前作品、它的季或集上的视频或游戏目录。"""
    season = aliased(Item)
    episode = aliased(Item)
    # 显式关联外层作品。否则季、集的子查询会和整张 items 做交叉连接，
    # 库里只要有一部剧挂着视频，所有作品都会被当成有文件。
    seasons = select(season.id).where(season.parent_id == Item.id).correlate(Item)
    episodes = select(episode.id).where(episode.parent_id.in_(seasons)).correlate(Item)
    return exists(
        select(MediaFile.id).where(
            MediaFile.file_kind.in_(MAIN_KINDS),
            or_(
                MediaFile.item_id == Item.id,
                MediaFile.item_id.in_(seasons),
                MediaFile.item_id.in_(episodes),
            ),
        )
    )


def drop_fileless_works(session: Session, library_id: int) -> int:
    """删掉这部库里已经没有任何视频或游戏目录的作品，连同季和集。"""
    ids = list(
        session.scalars(
            select(Item.id).where(
                Item.library_id == library_id,
                Item.kind == "work",
                ~work_has_main_file(),
            )
        )
    )
    if not ids:
        return 0
    children = select(Item.id).where(Item.parent_id.in_(ids))
    grandchildren = select(Item.id).where(Item.parent_id.in_(children))
    owned = select(Item.id).where(Item.id.in_(ids)).union(children, grandchildren)
    session.execute(delete(MediaFile).where(MediaFile.item_id.in_(owned)))
    session.execute(delete(Item).where(Item.id.in_(ids)))
    return len(ids)
