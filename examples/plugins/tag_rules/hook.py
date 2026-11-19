"""订阅 scrape.finished 的钩子：按设置页配的规则给标签。

写法上有五点：

1. 宿主在库提交之后才发事件，钩子在后台任务里跑，所以用 `host.get_item` 读到的是刮削后的最新值。
2. 规则从 `host.settings` 读：类上声明了 `Settings`，设置页就按它画表单，按钩子 id 保存，
   宿主把校验过的实例交给钩子；没保存过时是模型的默认值。
3. 只管自己名下的标签。`add_tags` 记在本插件名下，`remove_tags` 只能撤本插件加的，
   刮削结果、用户手动加的标签碰不到。
4. 幂等：同一个条目刮削多次、事件重复投递，结果都一样。`add_tags` 对已经加过的标签返回
   `unchanged`，不会重复写；只有真的有变化才调用接口，省额度。
5. 锁定的字段、用户手动删除过的标签，宿主会拒绝写入并在结果里列出，不是错误，不用重试。
"""

from mortis.plugin_api import Event, HookHost, HookPlugin, ScrapeFinished

from .rules import TAG_KIND, RuleSettings, own_tags, wanted_tags


class RuleTagger(HookPlugin):
    id = "example.tag_rules_tagger"
    name = "示例：按规则补标签"
    Settings = RuleSettings

    def on_event(self, host: HookHost, event: Event) -> None:
        if not isinstance(event, ScrapeFinished):
            return
        item = host.get_item(event.item_id)
        if item is None:
            return
        settings = host.settings
        assert isinstance(settings, RuleSettings)
        wanted = wanted_tags(item, settings)
        shown = set(item.tags.get(TAG_KIND, ()))
        if wanted:
            host.add_tags(item.id, TAG_KIND, wanted)
        # 规则变了（比如片长改了、规则关掉了）要撤掉不再适用的旧标签：只在本插件可能加的范围里撤，
        # 而且只撤现在还显示着的。不是本插件加的，宿主会拒绝并记在 not_owned 里。
        stale = sorted(name for name in own_tags(settings) if name in shown and name not in wanted)
        if stale:
            host.remove_tags(item.id, TAG_KIND, stale)
