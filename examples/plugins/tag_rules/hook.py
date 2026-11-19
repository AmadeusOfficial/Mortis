"""订阅 scrape.finished 的钩子：按规则给标签。

写法上有四点：

1. 宿主在库提交之后才发事件，钩子在后台任务里跑，所以用 `host.get_item` 读到的是刮削后的最新值。
2. 只管自己名下的标签。`add_tags` 记在本插件名下，`remove_tags` 只能撤本插件加的，
   刮削结果、用户手动加的标签碰不到。
3. 幂等：同一个条目刮削多次、事件重复投递，结果都一样。`add_tags` 对已经加过的标签返回
   `unchanged`，不会重复写；只有真的有变化才调用接口，省额度。
4. 锁定的字段、用户手动删除过的标签，宿主会拒绝写入并在结果里列出，不是错误，不用重试。
"""

from mortis.plugin_api import Event, HookHost, HookPlugin, ScrapeFinished

from .rules import OWN_TAGS, TAG_KIND, wanted_tags


class RuleTagger(HookPlugin):
    id = "example.tag_rules_tagger"
    name = "示例：按规则补标签"

    def on_event(self, host: HookHost, event: Event) -> None:
        if not isinstance(event, ScrapeFinished):
            return
        item = host.get_item(event.item_id)
        if item is None:
            return
        wanted = wanted_tags(item)
        shown = set(item.tags.get(TAG_KIND, ()))
        if wanted:
            host.add_tags(item.id, TAG_KIND, wanted)
        # 规则变了（比如片长改了）要撤掉不再适用的旧标签：只在本插件可能加的范围里撤，
        # 而且只撤现在还显示着的。不是本插件加的，宿主会拒绝并记在 not_owned 里。
        stale = [name for name in OWN_TAGS if name in shown and name not in wanted]
        if stale:
            host.remove_tags(item.id, TAG_KIND, stale)
