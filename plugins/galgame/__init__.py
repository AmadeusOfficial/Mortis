from mortis.plugin_api import DataTypePlugin, DataTypeSpec, FieldSpec

from .sources.bangumi import BangumiSource
from .sources.cngal import CngalSource
from .sources.vndb import VndbSource
from .sources.ymgal import YmgalSource

BASIC = "基本信息"
CREW = "制作人员"
IMAGES = "图片"


def _column(key: str, label: str, value_type: str, column: str, **extra: bool) -> FieldSpec:
    section = IMAGES if value_type == "images" else BASIC
    return FieldSpec(
        field_key=key,
        label=label,
        value_type=value_type,
        stored_in="items_column",
        column_name=column,
        detail_section=section,
        **extra,
    )


def _extra(key: str, label: str, value_type: str) -> FieldSpec:
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


def _people(key: str, label: str, role: str, **extra: bool) -> FieldSpec:
    return FieldSpec(
        field_key=key,
        label=label,
        value_type="people",
        stored_in="people",
        person_role=role,
        detail_section=CREW,
        **extra,
    )


GALGAME = DataTypeSpec(
    id="galgame",
    name="Galgame",
    structure="item",
    subtitle_field="brands",
    fields=[
        _column("title", "游戏名", "text", "title", is_sortable=True),
        _column("original_title", "原名", "text", "original_title"),
        _column("overview", "简介", "longtext", "overview"),
        _column("release_date", "发售日", "date", "release_date", is_sortable=True),
        FieldSpec(
            field_key="ratings",
            label="评分",
            value_type="rating",
            stored_in="ratings",
            detail_section=BASIC,
            is_sortable=True,
        ),
        _tags("brands", "品牌", "brand"),
        _tags("tags", "标签", "galgame_tag"),
        _extra("engine", "引擎", "text"),
        _extra("play_time", "游玩时长（小时）", "number"),
        _extra("website", "官网", "url"),
        _people("voice_actors", "声优", "voice", is_filterable=True),
        _people("scenario", "剧本", "writer", is_filterable=True),
        _people("artists", "原画", "artist", is_filterable=True),
        _column("poster", "封面", "images", "poster_path"),
        FieldSpec(
            field_key="fanart",
            label="背景图",
            value_type="images",
            stored_in="images",
            image_kind="fanart",
            detail_section=IMAGES,
        ),
    ],
)

PLUGIN = DataTypePlugin(
    spec=GALGAME,
    default_orders={
        "": ["bangumi", "ymgal", "vndb", "cngal"],
        "engine": ["vndb"],
        "play_time": ["vndb"],
        "ratings": ["bangumi", "vndb"],
        "poster": ["vndb", "bangumi", "ymgal", "cngal"],
        "tags": ["bangumi", "vndb"],
    },
    sources=(BangumiSource(), VndbSource(), YmgalSource(), CngalSource()),
)
