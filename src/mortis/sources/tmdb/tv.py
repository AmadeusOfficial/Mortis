from typing import Any

from mortis.config.models import TvScrapeConfig
from mortis.sources.tmdb.client import TmdbClient
from mortis.sources.tmdb.common import (
    compact,
    crew,
    image_candidates,
    image_languages,
    image_list,
    image_ranker,
    language_code,
    names,
    person_of,
    ratings,
    trailer,
)
from mortis.sources.tmdb.names import country_name

APPEND = "aggregate_credits,images,content_ratings,keywords,videos,external_ids"
STATUS = {
    "Returning Series": "连载中",
    "Ended": "已完结",
    "Canceled": "已取消",
    "In Production": "制作中",
    "Planned": "计划中",
    "Pilot": "试播",
}


def fetch_show(tmdb: TmdbClient, tmdb_id: int, config: TvScrapeConfig) -> dict[str, Any]:
    languages = image_languages(config)
    return tmdb.get(
        f"/tv/{tmdb_id}",
        {
            "language": config.language,
            "append_to_response": APPEND,
            "include_image_language": languages,
            "include_video_language": languages,
        },
    )


def fetch_season(
    tmdb: TmdbClient,
    tmdb_id: int,
    season: int,
    config: TvScrapeConfig,
    original_language: str | None,
) -> dict[str, Any]:
    """季详情；另按剧的原语言再请求一次，集原名放在 original_names（集号 → 名字）。"""
    path = f"/tv/{tmdb_id}/season/{season}"
    raw = tmdb.get(
        path,
        {
            "language": config.language,
            "append_to_response": "images",
            "include_image_language": image_languages(config),
        },
    )
    episodes = raw.get("episodes") or []
    if original_language and original_language != language_code(config):
        episodes = tmdb.get(path, {"language": original_language}).get("episodes") or []
    raw["original_names"] = {
        str(entry["episode_number"]): entry["name"]
        for entry in episodes
        if entry.get("episode_number") is not None and entry.get("name")
    }
    return raw


def _aggregate_cast(credits: dict[str, Any]) -> list[dict[str, Any]]:
    actors: list[dict[str, Any]] = []
    for entry in sorted(credits.get("cast") or [], key=lambda e: e.get("order", 0)):
        if entry.get("id") is None:
            continue
        person = person_of(entry)
        roles = [
            role["character"].strip()
            for role in entry.get("roles") or []
            if (role.get("character") or "").strip()
        ]
        person["character"] = " / ".join(dict.fromkeys(roles)) or None
        actors.append(person)
    return actors


def _guests(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    actors: list[dict[str, Any]] = []
    for entry in sorted(entries, key=lambda e: e.get("order", 0)):
        if entry.get("id") is None:
            continue
        person = person_of(entry)
        person["character"] = (entry.get("character") or "").strip() or None
        actors.append(person)
    return actors


def _certification(raw: dict[str, Any], country: str) -> str | None:
    for entry in (raw.get("content_ratings") or {}).get("results") or []:
        if entry.get("iso_3166_1") == country and entry.get("rating"):
            return entry["rating"]
    return None


def _runtime(raw: dict[str, Any]) -> int | None:
    """剧没写集时长时，用最近一集的时长。"""
    runtimes = raw.get("episode_run_time") or []
    if runtimes:
        return int(runtimes[0])
    last = raw.get("last_episode_to_air") or {}
    return last.get("runtime") or None


def show_candidates(raw: dict[str, Any], config: TvScrapeConfig) -> dict[str, Any]:
    countries = [
        country_name(code) for code in raw.get("origin_country") or [] if country_name(code)
    ]
    return compact(
        {
            "title": raw.get("name"),
            "original_title": raw.get("original_name"),
            "overview": raw.get("overview"),
            "release_date": raw.get("first_air_date"),
            "status": STATUS.get(raw.get("status") or "", raw.get("status")),
            "runtime": _runtime(raw),
            "certification": _certification(raw, config.certification_country),
            "ratings": ratings(raw),
            "genres": names(raw.get("genres"), "name"),
            "countries": list(dict.fromkeys(countries)),
            "studios": names(
                [*(raw.get("networks") or []), *(raw.get("production_companies") or [])], "name"
            ),
            "keywords": names((raw.get("keywords") or {}).get("results"), "name"),
            "trailer": trailer(raw, config),
            "actors": _aggregate_cast(raw.get("aggregate_credits") or {}),
            **image_candidates(raw.get("images") or {}, config),
        }
    )


def season_candidates(raw: dict[str, Any], config: TvScrapeConfig) -> dict[str, Any]:
    images = raw.get("images") or {}
    posters = images.get("posters") or []
    if not posters and raw.get("poster_path"):
        posters = [{"file_path": raw["poster_path"]}]
    return compact(
        {
            "title": raw.get("name"),
            "overview": raw.get("overview"),
            "release_date": raw.get("air_date"),
            "poster": image_list(posters, config.poster_size, image_ranker(config)),
        }
    )


def episode_candidates(
    raw: dict[str, Any], config: TvScrapeConfig, original_title: str | None = None
) -> dict[str, Any]:
    credits = {"crew": raw.get("crew") or []}
    still = raw.get("still_path")
    return compact(
        {
            "title": raw.get("name"),
            "original_title": original_title,
            "overview": raw.get("overview"),
            "release_date": raw.get("air_date"),
            "runtime": raw.get("runtime") or None,
            "ratings": ratings(raw),
            "actors": _guests(raw.get("guest_stars") or []),
            "directors": crew(credits, lambda e: e.get("job") == "Director"),
            "writers": crew(credits, lambda e: e.get("department") == "Writing"),
            "producers": crew(
                credits,
                lambda e: (
                    e.get("department") == "Production" and "Producer" in (e.get("job") or "")
                ),
            ),
            "thumb": [
                {
                    "url": TmdbClient.image_url(still, config.still_size),
                    "language": None,
                    "width": None,
                    "height": None,
                }
            ]
            if still
            else [],
        }
    )


def show_external_ids(raw: dict[str, Any]) -> dict[str, str]:
    external = raw.get("external_ids") or {}
    ids: dict[str, str] = {}
    if external.get("imdb_id"):
        ids["imdb"] = str(external["imdb_id"])
    if external.get("tvdb_id"):
        ids["tvdb"] = str(external["tvdb_id"])
    return ids
