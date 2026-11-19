"""标签规则：设置模型和按设置算标签的纯函数。只看条目的只读视图，不联网、不写库，方便单独测试。

设置页按 `RuleSettings` 自动画表单：数字框、文本框、开关，`rules` 是嵌套模型的列表，
页面上自带「+ 添加」、删除和上下移。字段上 `json_schema_extra` 里的 `ui:` 键是给表单的提示。
"""

import re

from pydantic import BaseModel, Field, field_validator

from mortis.plugin_api import ItemView

TAG_KIND = "keyword"
"""电影字段「标签」（keywords）对应的标签类别；写它要在 plugin.toml 声明 `tags:keyword`。"""


# 模型的 docstring 会变成 schema 的 description，在每个列表项上显示，所以这里用注释。
class Rule(BaseModel):
    pattern: str = Field(
        title="标题正则",
        description="在标题和原标题里查找，不区分大小写，找到就加标签",
        json_schema_extra={
            "ui:placeholder": "例如 ^(异形|Alien)",
            "ui:help": "Python 正则表达式；只想匹配整个标题时用 ^ 和 $ 括起来",
        },
    )
    tag: str = Field(
        title="标签", min_length=1, json_schema_extra={"ui:placeholder": "例如 异形系列"}
    )
    enabled: bool = Field(True, title="启用")

    @field_validator("pattern")
    @classmethod
    def _compiles(cls, value: str) -> str:
        try:
            re.compile(value)
        except re.error as error:
            raise ValueError(f"不是合法的正则表达式：{error}") from None
        return value


class RuleSettings(BaseModel):
    long_minutes: int = Field(150, ge=1, title="长片下限（分钟）")
    long_tag: str = Field("长片", min_length=1, title="长片标签")
    short_minutes: int = Field(90, ge=1, title="短片上限（分钟）")
    short_tag: str = Field("短片", min_length=1, title="短片标签")
    old_before_year: int = Field(1980, title="老电影年份（早于这一年）")
    old_tag: str = Field("老电影", min_length=1, title="老电影标签")
    rules: list[Rule] = Field(
        default_factory=list,
        title="按标题打标签",
        description="每条规则匹配上标题就加一个标签，可以先关掉不删",
    )


def own_tags(settings: RuleSettings) -> set[str]:
    """按当前设置，这个插件可能加的全部标签（含关掉的规则）。撤旧标签时只在这个范围里撤。

    设置里改了标签名，旧名字就不在范围里了，已经加上的旧标签要用户自己删。
    """
    names = {settings.long_tag, settings.short_tag, settings.old_tag}
    return names | {rule.tag for rule in settings.rules}


def wanted_tags(item: ItemView, settings: RuleSettings) -> list[str]:
    """按设置算出这个条目现在应该有哪些标签。同样的输入永远给出同样的结果。"""
    minutes = item.fields.get("runtime")
    found: list[str] = []
    if isinstance(minutes, int | float) and not isinstance(minutes, bool) and minutes > 0:
        if minutes >= settings.long_minutes:
            found.append(settings.long_tag)
        elif minutes <= settings.short_minutes:
            found.append(settings.short_tag)
    if item.year is not None and item.year < settings.old_before_year:
        found.append(settings.old_tag)
    titles = [text for text in (item.title, item.original_title) if text]
    for rule in settings.rules:
        if rule.enabled and any(re.search(rule.pattern, text, re.IGNORECASE) for text in titles):
            found.append(rule.tag)
    return list(dict.fromkeys(found))
