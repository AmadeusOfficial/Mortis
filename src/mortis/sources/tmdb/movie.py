from collections.abc import Callable, Iterable
from typing import Any

from mortis.config.models import MovieScrapeConfig
from mortis.sources.tmdb.client import TmdbClient

SOURCE = "tmdb"
APPEND = "credits,images,release_dates,keywords,videos,external_ids"
PROFILE_SIZE = "w185"
LOGO_SIZE = "original"
THEATRICAL = 3
EMPTY: tuple[Any, ...] = (None, "", [], {})


def _language(config: MovieScrapeConfig) -> str:
    return config.language.split("-")[0]


def _image_languages(config: MovieScrapeConfig) -> str:
    return f"{_language(config)},en,null"


def fetch_movie(tmdb: TmdbClient, tmdb_id: int, config: MovieScrapeConfig) -> dict[str, Any]:
    languages = _image_languages(config)
    return tmdb.get(
        f"/movie/{tmdb_id}",
        {
            "language": config.language,
            "append_to_response": APPEND,
            "include_image_language": languages,
            "include_video_language": languages,
        },
    )


def fetch_collection(
    tmdb: TmdbClient, collection_id: int, config: MovieScrapeConfig
) -> dict[str, Any]:
    raw = tmdb.get(f"/collection/{collection_id}", {"language": config.language})
    raw["images"] = tmdb.get(
        f"/collection/{collection_id}/images",
        {"include_image_language": _image_languages(config)},
    )
    return raw


def _ranker(order: list[str | None]) -> Callable[[str | None], int]:
    positions = {language: index for index, language in enumerate(order)}
    return lambda language: positions.get(language, len(order))


def _images(
    entries: Iterable[dict[str, Any]], size: str, rank: Callable[[str | None], int]
) -> list[dict[str, Any]]:
    ordered = sorted(
        (entry for entry in entries if entry.get("file_path")),
        key=lambda entry: (
            rank(entry.get("iso_639_1")),
            -(entry.get("vote_average") or 0),
            -(entry.get("width") or 0),
        ),
    )
    return [
        {
            "url": TmdbClient.image_url(entry["file_path"], size),
            "language": entry.get("iso_639_1"),
            "width": entry.get("width"),
            "height": entry.get("height"),
        }
        for entry in ordered
    ]


def _image_candidates(images: dict[str, Any], config: MovieScrapeConfig) -> dict[str, Any]:
    language = _language(config)
    normal = _ranker([language, "en", None])
    fanart = _ranker([None, language, "en"]) if config.prefer_textless_fanart else normal
    posters = images.get("posters") or []
    return {
        "poster": _images(posters, config.poster_size, normal),
        "fanart": _images(images.get("backdrops") or [], config.fanart_size, fanart),
        "clearlogo": _images(images.get("logos") or [], LOGO_SIZE, normal),
        "keyart": _images(
            [entry for entry in posters if entry.get("iso_639_1") is None],
            config.poster_size,
            normal,
        ),
    }


def _names(entries: Iterable[dict[str, Any]] | None, *keys: str) -> list[str]:
    result: list[str] = []
    for entry in entries or []:
        name = next((entry.get(key) for key in keys if entry.get(key)), None)
        if name and name not in result:
            result.append(name)
    return result


def _person(entry: dict[str, Any]) -> dict[str, Any]:
    profile = entry.get("profile_path")
    return {
        "tmdb_id": int(entry["id"]),
        "name": entry.get("name") or entry.get("original_name") or "",
        "original_name": entry.get("original_name") or None,
        "character": None,
        "photo_url": TmdbClient.image_url(profile, PROFILE_SIZE) if profile else None,
    }


def _cast(credits: dict[str, Any]) -> list[dict[str, Any]]:
    merged: dict[int, dict[str, Any]] = {}
    for entry in sorted(credits.get("cast") or [], key=lambda e: e.get("order", 0)):
        if entry.get("id") is None:
            continue
        character = (entry.get("character") or "").strip()
        person = merged.get(entry["id"])
        if person is None:
            person = _person(entry)
            person["character"] = character or None
            merged[entry["id"]] = person
        elif character:
            known = (person["character"] or "").split(" / ")
            if character not in known:
                person["character"] = (
                    f"{person['character']} / {character}" if person["character"] else character
                )
    return list(merged.values())


def _crew(
    credits: dict[str, Any], matches: Callable[[dict[str, Any]], bool]
) -> list[dict[str, Any]]:
    merged: dict[int, dict[str, Any]] = {}
    for entry in credits.get("crew") or []:
        if entry.get("id") is not None and matches(entry) and entry["id"] not in merged:
            merged[entry["id"]] = _person(entry)
    return list(merged.values())


def _certification(raw: dict[str, Any], country: str) -> str | None:
    for region in (raw.get("release_dates") or {}).get("results") or []:
        if region.get("iso_3166_1") != country:
            continue
        dates = sorted(
            region.get("release_dates") or [], key=lambda entry: entry.get("type") != THEATRICAL
        )
        for entry in dates:
            if entry.get("certification"):
                return entry["certification"]
    return None


def _trailer(raw: dict[str, Any], config: MovieScrapeConfig) -> str | None:
    rank = _ranker([_language(config), "en"])
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


def _ratings(raw: dict[str, Any]) -> list[dict[str, Any]]:
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


def _compact(values: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in values.items() if value not in EMPTY}


def movie_candidates(raw: dict[str, Any], config: MovieScrapeConfig) -> dict[str, Any]:
    credits = raw.get("credits") or {}
    return _compact(
        {
            "title": raw.get("title"),
            "original_title": raw.get("original_title"),
            "overview": raw.get("overview"),
            "tagline": raw.get("tagline"),
            "release_date": raw.get("release_date"),
            "runtime": raw.get("runtime") or None,
            "certification": _certification(raw, config.certification_country),
            "ratings": _ratings(raw),
            "genres": _names(raw.get("genres"), "name"),
            "countries": _names(raw.get("production_countries"), "name"),
            "studios": _names(raw.get("production_companies"), "name"),
            "keywords": _names((raw.get("keywords") or {}).get("keywords"), "name"),
            "spoken_languages": _names(raw.get("spoken_languages"), "english_name", "name"),
            "trailer": _trailer(raw, config),
            "directors": _crew(credits, lambda e: e.get("job") == "Director"),
            "writers": _crew(credits, lambda e: e.get("department") == "Writing"),
            "producers": _crew(
                credits,
                lambda e: (
                    e.get("department") == "Production" and "Producer" in (e.get("job") or "")
                ),
            ),
            "actors": _cast(credits),
            **_image_candidates(raw.get("images") or {}, config),
        }
    )


def collection_candidates(raw: dict[str, Any], config: MovieScrapeConfig) -> dict[str, Any]:
    images = _image_candidates(raw.get("images") or {}, config)
    return _compact(
        {
            "title": raw.get("name"),
            "original_title": raw.get("original_name"),
            "overview": raw.get("overview"),
            "poster": images["poster"],
            "fanart": images["fanart"],
        }
    )


def movie_external_ids(raw: dict[str, Any]) -> dict[str, str]:
    imdb = raw.get("imdb_id") or (raw.get("external_ids") or {}).get("imdb_id")
    return {"imdb": imdb} if imdb else {}
