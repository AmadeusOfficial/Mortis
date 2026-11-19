"""标签规则：只看条目的只读视图，不联网、不写库，方便单独测试。"""

from mortis.plugin_api import ItemView

TAG_KIND = "keyword"
"""电影字段「标签」（keywords）对应的标签类别；写它要在 plugin.toml 声明 `tags:keyword`。"""

LONG_MINUTES = 150
SHORT_MINUTES = 90
OLD_BEFORE_YEAR = 1980

LONG = "长片"
SHORT = "短片"
OLD = "老电影"
OWN_TAGS = (LONG, SHORT, OLD)
"""这个插件可能加的全部标签。规则变了要撤旧标签时，只在这个范围里撤。"""


def wanted_tags(item: ItemView) -> list[str]:
    """按规则算出这个条目现在应该有哪些标签。同样的输入永远给出同样的结果。"""
    minutes = item.fields.get("runtime")
    found: list[str] = []
    if isinstance(minutes, int | float) and not isinstance(minutes, bool) and minutes > 0:
        if minutes >= LONG_MINUTES:
            found.append(LONG)
        elif minutes <= SHORT_MINUTES:
            found.append(SHORT)
    if item.year is not None and item.year < OLD_BEFORE_YEAR:
        found.append(OLD)
    return found
