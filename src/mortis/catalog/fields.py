"""各种数据类型共用的字段分区和字段写法。"""

from mortis.catalog.spec import AppliesTo, FieldSpec, ValueType

BASIC = "基本信息"
CREW = "演职员"
IMAGES = "图片"


def column(
    key: str,
    label: str,
    value_type: ValueType,
    *,
    column: str | None = None,
    applies_to: AppliesTo = "work",
    section: str = BASIC,
    sortable: bool = False,
    filterable: bool = False,
) -> FieldSpec:
    return FieldSpec(
        field_key=key,
        label=label,
        value_type=value_type,
        stored_in="items_column",
        column_name=column or key,
        applies_to=applies_to,
        detail_section=section,
        is_sortable=sortable,
        is_filterable=filterable,
    )


def extra(
    key: str, label: str, value_type: ValueType, *, applies_to: AppliesTo = "work"
) -> FieldSpec:
    return FieldSpec(
        field_key=key,
        label=label,
        value_type=value_type,
        stored_in="fields",
        applies_to=applies_to,
        detail_section=BASIC,
    )


def tags(key: str, label: str, tag_kind: str, *, applies_to: AppliesTo = "work") -> FieldSpec:
    return FieldSpec(
        field_key=key,
        label=label,
        value_type="tags",
        stored_in="tags",
        tag_kind=tag_kind,
        applies_to=applies_to,
        detail_section=BASIC,
        is_filterable=True,
    )


def people(
    key: str,
    label: str,
    role: str,
    *,
    applies_to: AppliesTo = "work",
    filterable: bool = False,
) -> FieldSpec:
    return FieldSpec(
        field_key=key,
        label=label,
        value_type="people",
        stored_in="people",
        person_role=role,
        applies_to=applies_to,
        detail_section=CREW,
        is_filterable=filterable,
    )


def image(key: str, label: str, *, applies_to: AppliesTo = "work") -> FieldSpec:
    return FieldSpec(
        field_key=key,
        label=label,
        value_type="images",
        stored_in="images",
        image_kind=key,
        applies_to=applies_to,
        detail_section=IMAGES,
    )
