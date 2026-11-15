import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from mortis.catalog.install import InvalidDataType, ensure_builtin_types, install_data_type
from mortis.catalog.movie import MOVIE
from mortis.catalog.spec import DataTypeSpec, FieldSpec, validate_data_type
from mortis.db.models import DataType, TypeField


def _title(applies_to: str) -> FieldSpec:
    return FieldSpec(
        field_key="title",
        label="标题",
        value_type="text",
        stored_in="items_column",
        column_name="title",
        applies_to=applies_to,
        detail_section="基本信息",
    )


def _spec(*fields: FieldSpec, subtitle_field: str | None = None) -> DataTypeSpec:
    return DataTypeSpec(
        id="sample",
        name="样例",
        structure="item_group",
        subtitle_field=subtitle_field,
        fields=[_title("collection"), _title("work"), *fields],
    )


def _field(**overrides: object) -> FieldSpec:
    values: dict[str, object] = {
        "field_key": "extra",
        "label": "额外",
        "value_type": "text",
        "stored_in": "fields",
        "detail_section": "基本信息",
    }
    values.update(overrides)
    return FieldSpec.model_validate(values)


def test_movie_catalog_is_valid() -> None:
    assert validate_data_type(MOVIE) == []


def test_movie_catalog_covers_tmm_movie_fields() -> None:
    work = {f.field_key for f in MOVIE.fields if f.applies_to == "work"}
    assert {
        "title",
        "original_title",
        "sort_title",
        "tagline",
        "overview",
        "release_date",
        "runtime",
        "certification",
        "top250",
        "ratings",
        "genres",
        "countries",
        "studios",
        "keywords",
        "spoken_languages",
        "trailer",
        "directors",
        "writers",
        "producers",
        "actors",
        "poster",
        "fanart",
        "banner",
        "clearlogo",
        "clearart",
        "discart",
        "keyart",
        "thumb",
        "extrafanart",
    } == work
    collection = {f.field_key for f in MOVIE.fields if f.applies_to == "collection"}
    assert {
        "title",
        "original_title",
        "overview",
        "poster",
        "fanart",
        "banner",
        "clearlogo",
        "discart",
        "thumb",
    } == collection


def test_minimal_spec_is_valid() -> None:
    assert validate_data_type(_spec()) == []


@pytest.mark.parametrize(
    ("field", "message"),
    [
        (_field(applies_to="season"), "没有 season 这一层"),
        (_field(stored_in="tags", value_type="tags"), "必须填 tag_kind"),
        (
            _field(stored_in="tags", value_type="tags", tag_kind="genre", column_name="title"),
            "不能填 column_name",
        ),
        (_field(stored_in="items_column", column_name="runtime"), "没有共有列 runtime"),
        (
            _field(
                field_key="t2", stored_in="items_column", column_name="title", value_type="number"
            ),
            "列 title 不能存 number",
        ),
        (_field(value_type="tags"), "fields 不能存 tags"),
        (_field(is_filterable=True), "不能筛选"),
        (
            _field(stored_in="tags", value_type="tags", tag_kind="genre", is_sortable=True),
            "只有共有列和评分能排序",
        ),
        (_field(field_key="title", applies_to="work"), "字段键重复"),
        (_field(stored_in="ratings", value_type="number"), "ratings 不能存 number"),
    ],
)
def test_invalid_fields_are_reported(field: FieldSpec, message: str) -> None:
    errors = validate_data_type(_spec(field))
    assert any(message in error for error in errors), errors


def test_every_level_needs_title() -> None:
    spec = DataTypeSpec(id="sample", name="样例", structure="item_group", fields=[_title("work")])
    assert any("collection 层必须有 title" in error for error in validate_data_type(spec))


def test_subtitle_field_must_be_work_column_or_tags() -> None:
    errors = validate_data_type(_spec(_field(field_key="runtime"), subtitle_field="runtime"))
    assert any("subtitle_field" in error for error in errors), errors


def test_install_movie_writes_rows(session: Session) -> None:
    ensure_builtin_types(session)
    session.commit()

    row = session.get(DataType, "movie")
    assert row is not None
    assert (row.name, row.structure, row.subtitle_field, row.plugin_id, row.is_enabled) == (
        "电影",
        "item_group",
        "original_title",
        None,
        1,
    )
    count = session.scalar(select(func.count()).select_from(TypeField))
    assert count == len(MOVIE.fields)

    runtime = session.get(TypeField, ("movie", "work", "runtime"))
    assert runtime is not None
    assert (runtime.label, runtime.value_type, runtime.stored_in) == (
        "时长（分钟）",
        "number",
        "fields",
    )
    genres = session.get(TypeField, ("movie", "work", "genres"))
    assert genres is not None
    assert (genres.stored_in, genres.tag_kind, genres.is_filterable) == ("tags", "genre", 1)
    positions = session.scalars(select(TypeField.position).order_by(TypeField.position)).all()
    assert positions == list(range(1, len(MOVIE.fields) + 1))


def test_install_twice_is_idempotent(session: Session) -> None:
    ensure_builtin_types(session)
    session.commit()
    ensure_builtin_types(session)
    session.commit()
    assert session.scalar(select(func.count()).select_from(TypeField)) == len(MOVIE.fields)
    assert session.scalar(select(func.count()).select_from(DataType)) == 1


def test_reinstall_drops_removed_fields_and_keeps_is_enabled(session: Session) -> None:
    install_data_type(session, _spec(_field()))
    session.commit()
    session.get(DataType, "sample").is_enabled = 0
    session.commit()

    install_data_type(session, _spec())
    session.commit()

    keys = set(session.scalars(select(TypeField.field_key)).all())
    assert keys == {"title"}
    assert session.get(DataType, "sample").is_enabled == 0


def test_invalid_spec_is_rejected_without_writing(session: Session) -> None:
    with pytest.raises(InvalidDataType, match="sample"):
        install_data_type(session, _spec(_field(is_filterable=True)))
    assert session.get(DataType, "sample") is None
    assert session.scalar(select(func.count()).select_from(TypeField)) == 0
