import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any

from mortis.organize.names import ParsedName
from mortis.sources.tmdb.client import TmdbClient

_ARTICLE = re.compile(r"^(the|a|an)\s+")
EARLY_STOP = 0.95
RESULTS_PER_QUERY = 10


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


@dataclass(frozen=True)
class Match:
    hit: MovieHit
    score: float


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


def search_movies(tmdb: TmdbClient, query: str, year: int | None = None) -> list[MovieHit]:
    params: dict[str, Any] = {
        "query": query,
        "language": tmdb.language,
        "include_adult": "true" if tmdb.include_adult else "false",
    }
    if year is not None:
        params["year"] = year
    body = tmdb.get("/search/movie", params)
    return [_hit(raw) for raw in body.get("results", []) if raw.get("id")]


def _normalize(text: str) -> str:
    lowered = unicodedata.normalize("NFKC", text).casefold().strip()
    lowered = _ARTICLE.sub("", lowered)
    return "".join(ch for ch in lowered if ch.isalnum())


def similarity(left: str, right: str) -> float:
    a, b = _normalize(left), _normalize(right)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    return SequenceMatcher(None, a, b).ratio()


def score(hit: MovieHit, title: str, year: int | None) -> float:
    base = max(similarity(title, hit.title), similarity(title, hit.original_title))
    if year is None:
        return base
    if hit.year is None:
        return base - 0.05
    diff = abs(year - hit.year)
    if diff == 0:
        return min(1.0, base + 0.05)
    if diff == 1:
        return base
    return base - 0.15


def find_best(tmdb: TmdbClient, parsed: ParsedName) -> Match | None:
    """按解析出的几种标题依次搜，有年份先带年份搜，搜不到再不带年份搜。返回最高分，不管阈值。"""
    best: Match | None = None
    for query in parsed.search_titles:
        hits = search_movies(tmdb, query, parsed.year)
        if not hits and parsed.year is not None:
            hits = search_movies(tmdb, query, None)
        for candidate in hits[:RESULTS_PER_QUERY]:
            value = max(score(candidate, title, parsed.year) for title in parsed.search_titles)
            if best is None or value > best.score:
                best = Match(candidate, value)
        if best is not None and best.score >= EARLY_STOP:
            break
    return best
