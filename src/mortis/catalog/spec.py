from typing import Literal

from pydantic import BaseModel, Field

Structure = Literal["item", "item_group", "series"]
ValueType = Literal[
    "text", "longtext", "number", "date", "rating", "people", "tags", "images", "url"
]
StoredIn = Literal["items_column", "tags", "people", "images", "ratings", "fields"]
AppliesTo = Literal["collection", "work", "season", "episode"]

LEVELS: dict[str, tuple[str, ...]] = {
    "item": ("work",),
    "item_group": ("collection", "work"),
    "series": ("work", "season", "episode"),
}

# items 的共有列，以及每列能存的值类型。
COLUMN_VALUE_TYPES: dict[str, frozenset[str]] = {
    "title": frozenset({"text"}),
    "original_title": frozenset({"text"}),
    "sort_title": frozenset({"text"}),
    "overview": frozenset({"text", "longtext"}),
    "release_date": frozenset({"date"}),
    "poster_path": frozenset({"images"}),
}

STORE_VALUE_TYPES: dict[str, frozenset[str]] = {
    "tags": frozenset({"tags"}),
    "people": frozenset({"people"}),
    "images": frozenset({"images"}),
    "ratings": frozenset({"rating"}),
    "fields": frozenset({"text", "longtext", "number", "date", "url"}),
}

# 每种 stored_in 要求填的那一列；ratings 和 fields 不需要。
TARGET_ATTRIBUTE: dict[str, str] = {
    "items_column": "column_name",
    "tags": "tag_kind",
    "people": "person_role",
    "images": "image_kind",
}

KEY_PATTERN = r"^[a-z][a-z0-9_]*$"


class FieldSpec(BaseModel):
    field_key: str = Field(pattern=KEY_PATTERN)
    label: str = Field(min_length=1)
    value_type: ValueType
    stored_in: StoredIn
    column_name: str | None = None
    tag_kind: str | None = None
    person_role: str | None = None
    image_kind: str | None = None
    applies_to: AppliesTo = "work"
    detail_section: str = Field(min_length=1)
    is_lockable: bool = True
    is_filterable: bool = False
    is_sortable: bool = False


class DataTypeSpec(BaseModel):
    id: str = Field(pattern=KEY_PATTERN)
    name: str = Field(min_length=1)
    structure: Structure
    subtitle_field: str | None = None
    fields: list[FieldSpec]


def _field_errors(field: FieldSpec, structure: str) -> list[str]:
    name = f"{field.applies_to}.{field.field_key}"
    errors: list[str] = []
    if field.applies_to not in LEVELS[structure]:
        errors.append(f"{name}：结构 {structure} 没有 {field.applies_to} 这一层")

    target = TARGET_ATTRIBUTE.get(field.stored_in)
    for attribute in TARGET_ATTRIBUTE.values():
        value = getattr(field, attribute)
        if attribute == target and not value:
            errors.append(f"{name}：stored_in={field.stored_in} 时必须填 {attribute}")
        elif attribute != target and value is not None:
            errors.append(f"{name}：stored_in={field.stored_in} 时不能填 {attribute}")

    if field.stored_in == "items_column":
        if field.column_name:
            allowed = COLUMN_VALUE_TYPES.get(field.column_name)
            if allowed is None:
                errors.append(f"{name}：items 没有共有列 {field.column_name}")
            elif field.value_type not in allowed:
                errors.append(f"{name}：列 {field.column_name} 不能存 {field.value_type}")
    elif field.value_type not in STORE_VALUE_TYPES[field.stored_in]:
        errors.append(f"{name}：{field.stored_in} 不能存 {field.value_type}")

    if field.is_filterable and field.stored_in == "fields":
        errors.append(f"{name}：放在 fields 的字段不能筛选")
    if field.is_sortable and field.stored_in not in ("items_column", "ratings"):
        errors.append(f"{name}：只有共有列和评分能排序")
    return errors


def validate_data_type(spec: DataTypeSpec) -> list[str]:
    errors: list[str] = []
    seen: set[tuple[str, str]] = set()
    for field in spec.fields:
        key = (field.applies_to, field.field_key)
        if key in seen:
            errors.append(f"{field.applies_to}.{field.field_key}：字段键重复")
        seen.add(key)
        errors.extend(_field_errors(field, spec.structure))

    for level in LEVELS[spec.structure]:
        has_title = any(
            f.applies_to == level
            and f.field_key == "title"
            and f.stored_in == "items_column"
            and f.column_name == "title"
            for f in spec.fields
        )
        if not has_title:
            errors.append(f"{level} 层必须有 title 字段，存在 items 的 title 列")

    if spec.subtitle_field is not None:
        ok = any(
            f.applies_to == "work"
            and f.field_key == spec.subtitle_field
            and f.stored_in in ("items_column", "tags")
            for f in spec.fields
        )
        if not ok:
            errors.append(f"subtitle_field {spec.subtitle_field} 不是作品层的共有列或标签字段")
    return errors
