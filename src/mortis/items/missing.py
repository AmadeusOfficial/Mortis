"""缺集要不要出现。单部作品的选择优先，否则跟媒体库，再否则跟数据类型，默认显示。"""

from sqlalchemy.orm import Session

from mortis.db.models import Item, Library


def show_missing_episodes(session: Session, item: Item, type_default: bool) -> bool:
    if item.show_missing_episodes is not None:
        return bool(item.show_missing_episodes)
    library = session.get(Library, item.library_id)
    if library is not None and library.show_missing_episodes is not None:
        return bool(library.show_missing_episodes)
    return type_default
