from datetime import UTC, datetime

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    MetaData,
    PrimaryKeyConstraint,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    type_annotation_map = {str: Text}


class DataType(Base):
    __tablename__ = "data_types"
    __table_args__ = (
        CheckConstraint("structure in ('item', 'item_group', 'series')", name="structure"),
    )

    id: Mapped[str] = mapped_column(primary_key=True)
    name: Mapped[str]
    structure: Mapped[str]
    subtitle_field: Mapped[str | None]
    plugin_id: Mapped[str | None]
    is_enabled: Mapped[int] = mapped_column(server_default=text("1"))


class TypeField(Base):
    __tablename__ = "type_fields"
    __table_args__ = (
        PrimaryKeyConstraint("data_type_id", "applies_to", "field_key"),
        CheckConstraint(
            "value_type in ('text', 'longtext', 'number', 'date', 'rating', 'people', "
            "'tags', 'images', 'url')",
            name="value_type",
        ),
        CheckConstraint(
            "stored_in in ('items_column', 'tags', 'people', 'images', 'ratings', 'fields')",
            name="stored_in",
        ),
        CheckConstraint(
            "applies_to in ('collection', 'work', 'season', 'episode')", name="applies_to"
        ),
    )

    data_type_id: Mapped[str] = mapped_column(ForeignKey("data_types.id"))
    field_key: Mapped[str]
    label: Mapped[str]
    value_type: Mapped[str]
    stored_in: Mapped[str]
    column_name: Mapped[str | None]
    tag_kind: Mapped[str | None]
    person_role: Mapped[str | None]
    image_kind: Mapped[str | None]
    applies_to: Mapped[str] = mapped_column(server_default=text("'work'"))
    detail_section: Mapped[str]
    position: Mapped[int]
    is_lockable: Mapped[int] = mapped_column(server_default=text("1"))
    is_filterable: Mapped[int] = mapped_column(server_default=text("0"))
    is_sortable: Mapped[int] = mapped_column(server_default=text("0"))


class Library(Base):
    __tablename__ = "libraries"
    __table_args__ = (
        CheckConstraint("automation in ('manual', 'watch', 'schedule')", name="automation"),
        CheckConstraint(
            "wall_collections in ('movies', 'both', 'collapse')", name="wall_collections"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    data_type_id: Mapped[str] = mapped_column(ForeignKey("data_types.id"))
    name: Mapped[str]
    automation: Mapped[str] = mapped_column(server_default=text("'manual'"))
    is_auto_scrape: Mapped[int] = mapped_column(server_default=text("1"))
    # 海报墙怎么显示系列电影，只对 item_group 结构的库生效。
    wall_collections: Mapped[str] = mapped_column(server_default=text("'collapse'"))
    created_at: Mapped[str]
    updated_at: Mapped[str]

    paths: Mapped[list[LibraryPath]] = relationship(
        order_by="LibraryPath.id", cascade="all, delete-orphan", passive_deletes=True
    )
    data_type: Mapped[DataType] = relationship()


class LibraryPath(Base):
    __tablename__ = "library_paths"

    id: Mapped[int] = mapped_column(primary_key=True)
    library_id: Mapped[int] = mapped_column(ForeignKey("libraries.id", ondelete="CASCADE"))
    path: Mapped[str] = mapped_column(unique=True)


class Item(Base):
    __tablename__ = "items"
    __table_args__ = (
        CheckConstraint("kind in ('collection', 'work', 'season', 'episode')", name="kind"),
        Index("ix_items_wall", "library_id", "parent_id", "release_date"),
        Index("ix_items_parent", "parent_id", "index_number"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    library_id: Mapped[int] = mapped_column(ForeignKey("libraries.id"))
    data_type_id: Mapped[str] = mapped_column(ForeignKey("data_types.id"))
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("items.id", ondelete="CASCADE"))
    kind: Mapped[str]
    index_number: Mapped[int | None]
    title: Mapped[str]
    original_title: Mapped[str | None]
    sort_title: Mapped[str | None]
    subtitle: Mapped[str | None]
    overview: Mapped[str | None]
    release_date: Mapped[str | None]
    poster_path: Mapped[str | None]
    fields: Mapped[str] = mapped_column(server_default=text("'{}'"))
    field_sources: Mapped[str] = mapped_column(server_default=text("'{}'"))
    locked_fields: Mapped[str] = mapped_column(server_default=text("'[]'"))
    created_at: Mapped[str]
    updated_at: Mapped[str]


class ItemExternalId(Base):
    __tablename__ = "item_external_ids"
    __table_args__ = (
        PrimaryKeyConstraint("item_id", "provider"),
        Index("ix_item_external_ids_lookup", "provider", "external_id"),
    )

    item_id: Mapped[int] = mapped_column(ForeignKey("items.id", ondelete="CASCADE"))
    provider: Mapped[str]
    external_id: Mapped[str]
    is_primary: Mapped[int] = mapped_column(server_default=text("0"))


class ItemSourceRecord(Base):
    __tablename__ = "item_source_records"
    __table_args__ = (PrimaryKeyConstraint("item_id", "source"),)

    item_id: Mapped[int] = mapped_column(ForeignKey("items.id", ondelete="CASCADE"))
    source: Mapped[str]
    fetched_at: Mapped[str]
    raw: Mapped[str]


class Person(Base):
    __tablename__ = "people"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]
    original_name: Mapped[str | None]
    photo_path: Mapped[str | None]
    created_at: Mapped[str]
    updated_at: Mapped[str]


class PersonExternalId(Base):
    __tablename__ = "person_external_ids"
    __table_args__ = (PrimaryKeyConstraint("person_id", "provider"),)

    person_id: Mapped[int] = mapped_column(ForeignKey("people.id", ondelete="CASCADE"))
    provider: Mapped[str]
    external_id: Mapped[str]


class ItemPerson(Base):
    __tablename__ = "item_people"
    __table_args__ = (
        PrimaryKeyConstraint("item_id", "person_id", "role"),
        Index("ix_item_people_person", "person_id", "role"),
    )

    item_id: Mapped[int] = mapped_column(ForeignKey("items.id", ondelete="CASCADE"))
    person_id: Mapped[int] = mapped_column(ForeignKey("people.id"))
    role: Mapped[str]
    character_name: Mapped[str | None]
    position: Mapped[int]


class Tag(Base):
    __tablename__ = "tags"
    __table_args__ = (UniqueConstraint("tag_kind", "name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tag_kind: Mapped[str]
    name: Mapped[str]


class ItemTag(Base):
    __tablename__ = "item_tags"
    __table_args__ = (
        PrimaryKeyConstraint("item_id", "tag_id"),
        Index("ix_item_tags_tag", "tag_id"),
    )

    item_id: Mapped[int] = mapped_column(ForeignKey("items.id", ondelete="CASCADE"))
    tag_id: Mapped[int] = mapped_column(ForeignKey("tags.id"))
    position: Mapped[int]


class ItemTagOrigin(Base):
    """标签是谁加的：scrape、插件 id、manual；manual_removed 是用户删除过的记号。"""

    __tablename__ = "item_tag_origins"
    __table_args__ = (
        PrimaryKeyConstraint("item_id", "tag_id", "origin"),
        Index("ix_item_tag_origins_tag", "tag_id"),
    )

    item_id: Mapped[int] = mapped_column(ForeignKey("items.id", ondelete="CASCADE"))
    tag_id: Mapped[int] = mapped_column(ForeignKey("tags.id", ondelete="CASCADE"))
    origin: Mapped[str]


class ItemImage(Base):
    __tablename__ = "item_images"
    __table_args__ = (Index("ix_item_images_item", "item_id", "image_kind", "position"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id", ondelete="CASCADE"))
    image_kind: Mapped[str]
    source: Mapped[str | None]
    url: Mapped[str | None]
    file_path: Mapped[str | None]
    language: Mapped[str | None]
    width: Mapped[int | None]
    height: Mapped[int | None]
    position: Mapped[int]


class ItemRating(Base):
    __tablename__ = "item_ratings"
    __table_args__ = (PrimaryKeyConstraint("item_id", "source"),)

    item_id: Mapped[int] = mapped_column(ForeignKey("items.id", ondelete="CASCADE"))
    source: Mapped[str]
    rating: Mapped[float]
    max_rating: Mapped[float]
    vote_count: Mapped[int | None]


class SourceOrder(Base):
    __tablename__ = "source_orders"
    __table_args__ = (PrimaryKeyConstraint("data_type_id", "field_key", "source"),)

    data_type_id: Mapped[str] = mapped_column(ForeignKey("data_types.id"))
    field_key: Mapped[str] = mapped_column(server_default=text("''"))
    source: Mapped[str]
    position: Mapped[int]
    is_excluded: Mapped[int] = mapped_column(server_default=text("0"))


class MediaFile(Base):
    __tablename__ = "media_files"
    __table_args__ = (
        CheckConstraint(
            "file_kind in ('video', 'subtitle', 'trailer', 'nfo', 'image', 'game')",
            name="file_kind",
        ),
        Index("ix_media_files_item", "item_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    library_id: Mapped[int] = mapped_column(ForeignKey("libraries.id"))
    item_id: Mapped[int | None] = mapped_column(ForeignKey("items.id", ondelete="SET NULL"))
    path: Mapped[str] = mapped_column(unique=True)
    file_kind: Mapped[str]
    size: Mapped[int | None]
    duration_seconds: Mapped[int | None]
    created_at: Mapped[str]
    updated_at: Mapped[str]


class Task(Base):
    __tablename__ = "tasks"
    __table_args__ = (
        Index("ix_tasks_status", "status", "created_at"),
        Index("ix_tasks_parent", "parent_task_id"),
        Index("ix_tasks_root", "root_task_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    task_type: Mapped[str]
    status: Mapped[str]
    priority: Mapped[int] = mapped_column(server_default=text("0"))
    parent_task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"))
    root_task_id: Mapped[int | None]
    payload: Mapped[str | None]
    result: Mapped[str | None]
    error: Mapped[str | None]
    created_at: Mapped[str]
    started_at: Mapped[str | None]
    finished_at: Mapped[str | None]


class Schedule(Base):
    __tablename__ = "schedules"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]
    cron: Mapped[str]
    task_type: Mapped[str]
    payload: Mapped[str | None]
    is_enabled: Mapped[int] = mapped_column(server_default=text("1"))
    last_run_at: Mapped[str | None]
    next_run_at: Mapped[str | None]
