from mortis.catalog.spec import AppliesTo, DataTypeSpec, FieldSpec, ValueType

BASIC = "基本信息"
CREW = "演职员"
IMAGES = "图片"


def _column(
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


def _extra(key: str, label: str, value_type: ValueType) -> FieldSpec:
    return FieldSpec(
        field_key=key, label=label, value_type=value_type, stored_in="fields", detail_section=BASIC
    )


def _tags(key: str, label: str, tag_kind: str) -> FieldSpec:
    return FieldSpec(
        field_key=key,
        label=label,
        value_type="tags",
        stored_in="tags",
        tag_kind=tag_kind,
        detail_section=BASIC,
        is_filterable=True,
    )


def _people(key: str, label: str, role: str, *, filterable: bool = False) -> FieldSpec:
    return FieldSpec(
        field_key=key,
        label=label,
        value_type="people",
        stored_in="people",
        person_role=role,
        detail_section=CREW,
        is_filterable=filterable,
    )


def _image(key: str, label: str, *, applies_to: AppliesTo = "work") -> FieldSpec:
    return FieldSpec(
        field_key=key,
        label=label,
        value_type="images",
        stored_in="images",
        image_kind=key,
        applies_to=applies_to,
        detail_section=IMAGES,
    )


MOVIE = DataTypeSpec(
    id="movie",
    name="电影",
    structure="item_group",
    subtitle_field="original_title",
    fields=[
        # 系列电影
        _column("title", "系列名", "text", applies_to="collection", sortable=True),
        _column("original_title", "原名", "text", applies_to="collection"),
        _column("overview", "简介", "longtext", applies_to="collection"),
        _column(
            "poster",
            "海报",
            "images",
            column="poster_path",
            applies_to="collection",
            section=IMAGES,
        ),
        _image("fanart", "同人画", applies_to="collection"),
        _image("banner", "横幅", applies_to="collection"),
        _image("clearlogo", "透明标志", applies_to="collection"),
        _image("discart", "光盘", applies_to="collection"),
        _image("thumb", "缩略图", applies_to="collection"),
        # 电影
        _column("title", "片名", "text", sortable=True),
        _column("original_title", "原名", "text"),
        _column("sort_title", "排序名", "text", sortable=True),
        _extra("tagline", "标语", "text"),
        _column("overview", "剧情", "longtext"),
        _column("release_date", "上映日期", "date", sortable=True, filterable=True),
        _extra("runtime", "时长（分钟）", "number"),
        _extra("certification", "分级", "text"),
        _extra("top250", "排名", "number"),
        FieldSpec(
            field_key="ratings",
            label="评分",
            value_type="rating",
            stored_in="ratings",
            detail_section=BASIC,
            is_sortable=True,
        ),
        _tags("genres", "类型", "genre"),
        _tags("countries", "国家/地区", "country"),
        _tags("studios", "制片公司", "studio"),
        _tags("keywords", "标签", "keyword"),
        _tags("spoken_languages", "对白语言", "language"),
        _extra("trailer", "预告片", "url"),
        _people("directors", "导演", "director", filterable=True),
        _people("writers", "编剧", "writer"),
        _people("producers", "制片", "producer"),
        _people("actors", "演员", "actor", filterable=True),
        _column("poster", "海报", "images", column="poster_path", section=IMAGES),
        _image("fanart", "同人画"),
        _image("banner", "横幅"),
        _image("clearlogo", "透明标志"),
        _image("clearart", "透明艺术图"),
        _image("discart", "光盘"),
        _image("keyart", "关键艺术图"),
        _image("thumb", "缩略图"),
        _image("extrafanart", "额外同人画"),
    ],
)
