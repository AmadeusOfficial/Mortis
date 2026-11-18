import re
import unicodedata
from dataclasses import dataclass, replace
from difflib import SequenceMatcher
from typing import Any, Literal

from mortis.organize.names import ParsedName
from mortis.sources.tmdb.client import TmdbClient, TmdbNotFound

_ARTICLE = re.compile(r"^(the|a|an)\s+")
# 剧的目录名常带季名或章节，TMDB 上却是总条目：`赌博默示录:破戒录篇` 要能搜到《赌博默示录》。
_SEASON_MARK = re.compile(r":|\s-\s|\s?[～~]")
_CJK = re.compile(r"[\u3040-\u30ff\u3400-\u9fff\uf900-\ufaff\uac00-\ud7af]")
_DIGITS = re.compile(r"[0-9]+")
_WORD = re.compile(r"[A-Za-z]+")
_ROMAN = {"ii": "2", "iii": "3", "iv": "4", "vi": "6", "vii": "7", "viii": "8"}
_NUMBER_WORDS = {"one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6"}
_FULL_ROMAN = str.maketrans({"Ⅰ": "1", "Ⅱ": "2", "Ⅲ": "3", "Ⅳ": "4", "Ⅴ": "5", "Ⅵ": "6"})
# 用中日韩语言搜时，结果只带译名和原名；英文文件名要靠英文标题才对得上。
_LOCAL_SCRIPT_LANGUAGES = ("zh", "ja", "ko")
FALLBACK_LANGUAGE = "en-US"
EARLY_STOP = 0.95
RESULTS_PER_QUERY = 10
ALIAS_CANDIDATES = 3
OBSCURE_RATIO = 0.1
SEQUEL_PENALTY = 0.3
NO_YEAR_PENALTY = 0.05


@dataclass(frozen=True)
class MovieHit:
    tmdb_id: int
    title: str
    original_title: str
    release_date: str | None
    year: int | None
    overview: str
    poster_path: str | None
    popularity: float
    aliases: tuple[str, ...] = ()
    """英文标题、别名这些只用来打分的名字。"""


@dataclass(frozen=True)
class Match:
    hit: MovieHit
    score: float
    rivals: tuple[MovieHit, ...] = ()
    """文件名没有年份时，名字同样完全一致、有上映年份的其他候选；有的话不自动绑定，交给人选。"""


def _hit(raw: dict[str, Any]) -> MovieHit:
    date = raw.get("release_date") or None
    year = int(date[:4]) if date and date[:4].isdigit() else None
    return MovieHit(
        tmdb_id=int(raw["id"]),
        title=raw.get("title") or raw.get("original_title") or "",
        original_title=raw.get("original_title") or "",
        release_date=date,
        year=year,
        overview=raw.get("overview") or "",
        poster_path=raw.get("poster_path") or None,
        popularity=float(raw.get("popularity") or 0),
    )


def search_movies(
    tmdb: TmdbClient, query: str, year: int | None = None, *, language: str | None = None
) -> list[MovieHit]:
    params: dict[str, Any] = {
        "query": query,
        "language": language or tmdb.language,
        "include_adult": "true" if tmdb.include_adult else "false",
    }
    if year is not None:
        params["year"] = year
    body = tmdb.get("/search/movie", params)
    return [_hit(raw) for raw in body.get("results", []) if raw.get("id")]


def _canon(text: str) -> str:
    """罗马数字和英文数词统一成阿拉伯数字：`Royal Tramp II` 和 `Royal Tramp 2` 是同一个名字。"""
    lowered = unicodedata.normalize("NFKC", text.translate(_FULL_ROMAN)).casefold().strip()
    return _WORD.sub(
        lambda m: _ROMAN.get(m.group(0), _NUMBER_WORDS.get(m.group(0), m.group(0))), lowered
    )


def _normalize(text: str) -> str:
    lowered = _ARTICLE.sub("", _canon(text))
    return "".join(ch for ch in lowered if ch.isalnum())


def _numbers(text: str) -> set[str]:
    return {str(int(number)) for number in _DIGITS.findall(_canon(text))}


def similarity(left: str, right: str) -> float:
    a, b = _normalize(left), _normalize(right)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    return SequenceMatcher(None, a, b).ratio()


def title_score(query: str, name: str) -> float:
    """名字相似度；文件名里的数字（续作号）候选名里没有，就不是同一部。"""
    value = similarity(query, name)
    if _numbers(query) - _numbers(name):
        value -= SEQUEL_PENALTY
    return value


def _names(hit: MovieHit) -> list[str]:
    return [name for name in (hit.title, hit.original_title, *hit.aliases) if name]


def score(hit: MovieHit, title: str, year: int | None) -> float:
    base = max((title_score(title, name) for name in _names(hit)), default=0.0)
    if hit.year is None:
        return base - NO_YEAR_PENALTY
    if year is None:
        return base
    diff = abs(year - hit.year)
    if diff == 0:
        return min(1.0, base + 0.05)
    if diff == 1:
        return base
    return base - 0.15


def search_tv(
    tmdb: TmdbClient, query: str, year: int | None = None, *, language: str | None = None
) -> list[MovieHit]:
    params: dict[str, Any] = {
        "query": query,
        "language": language or tmdb.language,
        "include_adult": "true" if tmdb.include_adult else "false",
    }
    if year is not None:
        params["first_air_date_year"] = year
    body = tmdb.get("/search/tv", params)
    return [
        _hit(
            {
                **raw,
                "title": raw.get("name"),
                "original_title": raw.get("original_name"),
                "release_date": raw.get("first_air_date"),
            }
        )
        for raw in body.get("results", [])
        if raw.get("id")
    ]


def _short_titles(title: str) -> list[str]:
    """冒号、「 - 」、「～/~」之前的部分；电影不改，只在剧里用作补充搜索。"""
    match = _SEASON_MARK.search(title)
    if match is None:
        return []
    short = title[: match.start()].strip(" -\u2013\u2014:([{_.")
    return [short] if len(short) >= 2 else []


def _attempts(parsed: ParsedName) -> list[tuple[str, int | None]]:
    """要比的（标题，年份）。片名末尾的数字可能被当成了年份（`Kim.Ji-young.Born.1982`），也带上不拆年份的那种。"""
    result = [(title, parsed.year) for title in parsed.search_titles]
    if parsed.year is not None:
        result.append((f"{parsed.title} {parsed.year}", None))
    return list(dict.fromkeys(result))


def _value(
    candidate: MovieHit,
    attempts: list[tuple[str, int | None]],
    shorts: tuple[str, ...],
    year: int | None,
) -> float:
    """缩短的标题是续作目录：年份晚于首播不扣分，早于首播扣得更重。"""
    value = max(score(candidate, title, title_year) for title, title_year in attempts)
    for title in shorts:
        base = max((title_score(title, name) for name in _names(candidate)), default=0.0)
        if year is not None and candidate.year is not None and year < candidate.year:
            base -= 0.3
        value = max(value, base)
    return value


def _needs_fallback(tmdb: TmdbClient, query: str) -> bool:
    primary = tmdb.language.split("-")[0].casefold()
    return primary in _LOCAL_SCRIPT_LANGUAGES and not _CJK.search(query)


class _Pool:
    """几次搜索合起来的候选。英文结果只补名字；只在英文结果里出现的，认出后再取一次本地语言的标题。"""

    def __init__(self) -> None:
        self.hits: dict[int, MovieHit] = {}
        self.local: set[int] = set()

    def add(self, hit: MovieHit, names: tuple[str, ...] = (), *, local: bool) -> None:
        known = self.hits.get(hit.tmdb_id, hit)
        aliases = tuple(dict.fromkeys((*known.aliases, *names)))
        self.hits[hit.tmdb_id] = replace(known, aliases=aliases)
        if local:
            self.local.add(hit.tmdb_id)

    def best(
        self, attempts: list[tuple[str, int | None]], shorts: tuple[str, ...], year: int | None
    ) -> Match | None:
        """同分时取热度高的：没有年份的同名片很多，热门的那部才是要找的。"""
        best: Match | None = None
        for hit in self.hits.values():
            value = _value(hit, attempts, shorts, year)
            if best is None or (value, hit.popularity) > (best.score, best.hit.popularity):
                best = Match(hit, value)
        return best


def _check_aliases(pool: _Pool, best: Match, threshold: float) -> bool:
    """分数不够，或者胜出的是冷门同名片。

    `Kaibutsu` 是一部 2013 年冷门片的英文名，也是是枝裕和《怪物》的别名。
    """
    if best.score < threshold:
        return True
    top = max(pool.hits[tmdb_id].popularity for tmdb_id in list(pool.hits)[:ALIAS_CANDIDATES])
    return best.hit.popularity < top * OBSCURE_RATIO


def _exact(hit: MovieHit, attempts: list[tuple[str, int | None]]) -> bool:
    return any(similarity(title, name) == 1.0 for title, _ in attempts for name in _names(hit))


def _rivals(
    pool: _Pool, best: Match, attempts: list[tuple[str, int | None]]
) -> tuple[MovieHit, ...]:
    if not _exact(best.hit, attempts):
        return ()
    return tuple(
        hit
        for hit in pool.hits.values()
        if hit.tmdb_id != best.hit.tmdb_id and hit.year is not None and _exact(hit, attempts)
    )


def _alternative_titles(tmdb: TmdbClient, kind: str, tmdb_id: int) -> tuple[str, ...]:
    try:
        body = tmdb.get(f"/{kind}/{tmdb_id}/alternative_titles")
    except TmdbNotFound:
        return ()
    rows = body.get("titles") or body.get("results") or []
    return tuple(row["title"] for row in rows if row.get("title"))


def _localized(tmdb: TmdbClient, kind: str, hit: MovieHit) -> MovieHit:
    try:
        body = tmdb.get(f"/{kind}/{hit.tmdb_id}", {"language": tmdb.language})
    except TmdbNotFound:
        return hit
    title = body.get("name" if kind == "tv" else "title") or hit.title
    original = body.get("original_name" if kind == "tv" else "original_title")
    return replace(
        hit,
        title=title,
        original_title=original or hit.original_title,
        aliases=tuple(dict.fromkeys((*hit.aliases, hit.title))),
    )


def find_best(
    tmdb: TmdbClient,
    parsed: ParsedName,
    *,
    kind: Literal["movie", "tv"] = "movie",
    threshold: float = 0.75,
) -> Match | None:
    """按解析出的几种标题依次搜，有年份先带年份搜，搜不到再不带年份搜。返回最高分，不管阈值。

    英文文件名再用英文搜一次，英文标题参与打分；
    最高分不到 threshold 或胜出的是冷门同名片时，取前几名候选的别名再比一次。
    """
    search = search_tv if kind == "tv" else search_movies
    shorts = tuple(
        short
        for short in (_short_titles(parsed.title) if kind == "tv" else [])
        if short not in parsed.search_titles
    )
    attempts = _attempts(parsed)
    queries = [*attempts, *((short, parsed.year) for short in shorts)]
    pool = _Pool()
    best: Match | None = None
    for query, year in queries:
        hits = search(tmdb, query, year)
        if not hits and year is not None:
            hits = search(tmdb, query, None)
        for candidate in hits[:RESULTS_PER_QUERY]:
            pool.add(candidate, local=True)
        if _needs_fallback(tmdb, query):
            english = search(tmdb, query, year, language=FALLBACK_LANGUAGE)
            if not english and year is not None:
                english = search(tmdb, query, None, language=FALLBACK_LANGUAGE)
            for candidate in english[:RESULTS_PER_QUERY]:
                pool.add(candidate, (candidate.title,), local=False)
        best = pool.best(attempts, shorts, parsed.year)
        if best is not None and best.score >= EARLY_STOP:
            break

    if best is not None and _check_aliases(pool, best, threshold):
        for tmdb_id in list(pool.hits)[:ALIAS_CANDIDATES]:
            pool.add(pool.hits[tmdb_id], _alternative_titles(tmdb, kind, tmdb_id), local=False)
        best = pool.best(attempts, shorts, parsed.year)

    if best is not None and parsed.year is None:
        best = replace(best, rivals=_rivals(pool, best, attempts))
    if best is not None and best.hit.tmdb_id not in pool.local:
        best = replace(best, hit=_localized(tmdb, kind, best.hit))
    return best
