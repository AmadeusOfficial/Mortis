"""用来源插件按标题找条目，打分和 TMDB 识别同一套。"""

from dataclasses import dataclass

from mortis.plugin_api import Hit, Host, SourcePlugin
from mortis.sources.tmdb.search import title_score

MATCH_THRESHOLD = 0.75
"""各类型设置里 match_threshold 的默认值。实际用哪个阈值由调用方从类型设置里读。"""
RESULTS_PER_QUERY = 10
EARLY_STOP = 0.95


@dataclass(frozen=True)
class SourceMatch:
    source: SourcePlugin
    hit: Hit
    score: float


def hit_score(hit: Hit, title: str, year: int | None) -> float:
    names = [name for name in (hit.title, hit.original_title, *hit.aliases) if name]
    base = max((title_score(title, name) for name in names), default=0.0)
    if year is None or hit.year is None:
        return base
    diff = abs(year - hit.year)
    if diff == 0:
        return min(1.0, base + 0.05)
    if diff == 1:
        return base
    if diff == 2:
        return base - 0.15
    return base - 0.45


def best_hit(
    host: Host, plugin: SourcePlugin, titles: list[str], year: int | None
) -> SourceMatch | None:
    """依次用每个标题搜，返回最高分，不管阈值。"""
    best: SourceMatch | None = None
    for query in dict.fromkeys(title for title in titles if title):
        for hit in plugin.search(host, query)[:RESULTS_PER_QUERY]:
            value = max(hit_score(hit, title, year) for title in titles if title)
            if best is None or value > best.score:
                best = SourceMatch(plugin, hit, value)
        if best is not None and best.score >= EARLY_STOP:
            break
    return best
