"""组合插件：一个来源加一个钩子，放在同一个包里。

- 来源 `example.tag_rules_films` 给电影补片长；
- 钩子 `example.tag_rules_tagger` 订阅 `scrape.finished`，刮削完按片长、年份和标题正则
  给「标签」类标签补几个；规则在设置页的钩子设置里配，默认只有片长和年份两条。

plugin.toml 的 `[[sources]]`、`[[hooks]]` 要和这里的对象按 id 一一对上，多了少了都会加载失败。
`PLUGIN = Plugin(...)` 是任意组合的写法，想带几样带几样。
"""

from mortis.plugin_api import Plugin

from .hook import RuleTagger
from .source import FilmSource

PLUGIN = Plugin(sources=(FilmSource(),), hooks=(RuleTagger(),))
