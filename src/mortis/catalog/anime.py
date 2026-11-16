from mortis.catalog.spec import DataTypeSpec
from mortis.catalog.tv import TV

# 动漫和电视剧同一种结构，字段目录直接复用电视剧的；设置、刮削、整理各走自己的一节。
ANIME = DataTypeSpec(
    id="anime",
    name="动漫",
    structure="series",
    subtitle_field="original_title",
    fields=TV.fields,
)
