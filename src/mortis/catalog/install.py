from sqlalchemy import delete
from sqlalchemy.orm import Session

from mortis.catalog.anime import ANIME
from mortis.catalog.movie import MOVIE
from mortis.catalog.spec import DataTypeSpec, validate_data_type
from mortis.catalog.tv import TV
from mortis.db.models import DataType, TypeField

BUILTIN_TYPES: tuple[DataTypeSpec, ...] = (MOVIE, TV, ANIME)


class InvalidDataType(ValueError):
    pass


def install_data_type(
    session: Session, spec: DataTypeSpec, *, plugin_id: str | None = None
) -> None:
    errors = validate_data_type(spec)
    if errors:
        raise InvalidDataType(f"数据类型 {spec.id} 的字段目录不合格：" + "；".join(errors))

    row = session.get(DataType, spec.id)
    if row is None:
        row = DataType(id=spec.id, is_enabled=1)
        session.add(row)
    row.name = spec.name
    row.structure = spec.structure
    row.subtitle_field = spec.subtitle_field
    row.plugin_id = plugin_id
    session.flush()

    session.execute(delete(TypeField).where(TypeField.data_type_id == spec.id))
    for position, field in enumerate(spec.fields, start=1):
        session.add(
            TypeField(
                data_type_id=spec.id,
                field_key=field.field_key,
                label=field.label,
                value_type=field.value_type,
                stored_in=field.stored_in,
                column_name=field.column_name,
                tag_kind=field.tag_kind,
                person_role=field.person_role,
                image_kind=field.image_kind,
                applies_to=field.applies_to,
                detail_section=field.detail_section,
                position=position,
                is_lockable=int(field.is_lockable),
                is_filterable=int(field.is_filterable),
                is_sortable=int(field.is_sortable),
            )
        )
    session.flush()


def ensure_builtin_types(session: Session) -> None:
    for spec in BUILTIN_TYPES:
        install_data_type(session, spec)
