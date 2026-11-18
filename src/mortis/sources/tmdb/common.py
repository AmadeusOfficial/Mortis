"""电影和电视剧共用的 TMDB 字段转换。"""

from collections.abc import Callable, Iterable
from typing import Any

from mortis.config.models import MovieScrapeConfig, TvScrapeConfig
from mortis.sources.tmdb.client import TmdbClient

SOURCE = "tmdb"
PROFILE_SIZE = "w185"
LOGO_SIZE = "original"
EMPTY: tuple[Any, ...] = (None, "", [], {})

type ScrapeConfig = MovieScrapeConfig | TvScrapeConfig


def language_code(config: ScrapeConfig) -> str:
    return config.language.split("-")[0]


def region_code(config: ScrapeConfig) -> str | None:
    parts = config.language.split("-")
    return parts[1].upper() if len(parts) > 1 and parts[1] else None


def image_languages(config: ScrapeConfig) -> str:
    return f"{language_code(config)},en,null"


def ranker(order: list[str | None]) -> Callable[[str | None], int]:
    positions = {language: index for index, language in enumerate(order)}
    return lambda language: positions.get(language, len(order))


type ImageRank = Callable[[dict[str, Any]], int]


def image_ranker(config: ScrapeConfig, *, textless_first: bool = False) -> ImageRank:
    """档位绝对优先，评分只在同一档里比：设置的语言和地区都对上、同语言其他地区、英文、无字、其他语言。

    textless_first 时无字排最前，其余次序不变。
    """
    language, region = language_code(config), region_code(config)

    def rank(entry: dict[str, Any]) -> int:
        code = entry.get("iso_639_1")
        if code is None:
            tier = 3
        elif code == language:
            tier = 0 if region is not None and entry.get("iso_3166_1") == region else 1
        elif code == "en":
            tier = 2
        else:
            tier = 4
        if textless_first:
            return -1 if tier == 3 else tier
        return tier

    return rank


def image_list(
    entries: Iterable[dict[str, Any]], size: str, rank: ImageRank
) -> list[dict[str, Any]]:
    ordered = sorted(
        (entry for entry in entries if entry.get("file_path")),
        key=lambda entry: (
            rank(entry),
            -(entry.get("vote_average") or 0),
            -(entry.get("width") or 0),
        ),
    )
    return [
        {
            "url": TmdbClient.image_url(entry["file_path"], size),
            "language": entry.get("iso_639_1"),
            "region": entry.get("iso_3166_1") if entry.get("iso_639_1") else None,
            "width": entry.get("width"),
            "height": entry.get("height"),
        }
        for entry in ordered
    ]


def image_candidates(images: dict[str, Any], config: ScrapeConfig) -> dict[str, Any]:
    normal = image_ranker(config)
    fanart = image_ranker(config, textless_first=True) if config.prefer_textless_fanart else normal
    posters = images.get("posters") or []
    return {
        "poster": image_list(posters, config.poster_size, normal),
        "fanart": image_list(images.get("backdrops") or [], config.fanart_size, fanart),
        "clearlogo": image_list(images.get("logos") or [], LOGO_SIZE, normal),
        "keyart": image_list(
            [entry for entry in posters if entry.get("iso_639_1") is None],
            config.poster_size,
            normal,
        ),
    }


def names(entries: Iterable[dict[str, Any]] | None, *keys: str) -> list[str]:
    result: list[str] = []
    for entry in entries or []:
        name = next((entry.get(key) for key in keys if entry.get(key)), None)
        if name and name not in result:
            result.append(name)
    return result


def coded(
    entries: Iterable[dict[str, Any]] | None,
    key: str,
    convert: Callable[[str | None], str | None],
) -> list[str]:
    result: list[str] = []
    for entry in entries or []:
        name = convert(entry.get(key))
        if name and name not in result:
            result.append(name)
    return result


def person_of(entry: dict[str, Any]) -> dict[str, Any]:
    profile = entry.get("profile_path")
    return {
        "tmdb_id": int(entry["id"]),
        "name": entry.get("name") or entry.get("original_name") or "",
        "original_name": entry.get("original_name") or None,
        "character": None,
        "photo_url": TmdbClient.image_url(profile, PROFILE_SIZE) if profile else None,
    }


def cast(credits: dict[str, Any]) -> list[dict[str, Any]]:
    merged: dict[int, dict[str, Any]] = {}
    for entry in sorted(credits.get("cast") or [], key=lambda e: e.get("order", 0)):
        if entry.get("id") is None:
            continue
        character = (entry.get("character") or "").strip()
        person = merged.get(entry["id"])
        if person is None:
            person = person_of(entry)
            person["character"] = character or None
            merged[entry["id"]] = person
        elif character:
            known = (person["character"] or "").split(" / ")
            if character not in known:
                person["character"] = (
                    f"{person['character']} / {character}" if person["character"] else character
                )
    return list(merged.values())


def crew(
    credits: dict[str, Any], matches: Callable[[dict[str, Any]], bool]
) -> list[dict[str, Any]]:
    merged: dict[int, dict[str, Any]] = {}
    for entry in credits.get("crew") or []:
        if entry.get("id") is not None and matches(entry) and entry["id"] not in merged:
            merged[entry["id"]] = person_of(entry)
    return list(merged.values())


def trailer(raw: dict[str, Any], config: ScrapeConfig) -> str | None:
    rank = ranker([language_code(config), "en"])
    trailers = sorted(
        (
            video
            for video in (raw.get("videos") or {}).get("results") or []
            if video.get("site") == "YouTube"
            and video.get("type") == "Trailer"
            and video.get("key")
        ),
        key=lambda video: rank(video.get("iso_639_1")),
    )
    return f"https://www.youtube.com/watch?v={trailers[0]['key']}" if trailers else None


def ratings(raw: dict[str, Any]) -> list[dict[str, Any]]:
    if not raw.get("vote_count"):
        return []
    return [
        {
            "source": SOURCE,
            "rating": float(raw.get("vote_average") or 0),
            "max_rating": 10.0,
            "vote_count": int(raw["vote_count"]),
        }
    ]


def compact(values: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in values.items() if value not in EMPTY}
