from typing import Any

from mortis.config.models import MovieScrapeConfig
from mortis.sources.tmdb.client import TmdbClient
from mortis.sources.tmdb.common import (
    cast,
    coded,
    compact,
    crew,
    image_candidates,
    image_languages,
    names,
    ratings,
    trailer,
)
from mortis.sources.tmdb.names import country_name, language_name

APPEND = "credits,images,release_dates,keywords,videos,external_ids"
THEATRICAL = 3


def fetch_movie(tmdb: TmdbClient, tmdb_id: int, config: MovieScrapeConfig) -> dict[str, Any]:
    languages = image_languages(config)
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
        {"include_image_language": image_languages(config)},
    )
    return raw


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


def movie_candidates(raw: dict[str, Any], config: MovieScrapeConfig) -> dict[str, Any]:
    credits = raw.get("credits") or {}
    return compact(
        {
            "title": raw.get("title"),
            "original_title": raw.get("original_title"),
            "overview": raw.get("overview"),
            "tagline": raw.get("tagline"),
            "release_date": raw.get("release_date"),
            "runtime": raw.get("runtime") or None,
            "certification": _certification(raw, config.certification_country),
            "ratings": ratings(raw),
            "genres": names(raw.get("genres"), "name"),
            "countries": coded(raw.get("production_countries"), "iso_3166_1", country_name),
            "studios": names(raw.get("production_companies"), "name"),
            "keywords": names((raw.get("keywords") or {}).get("keywords"), "name"),
            "spoken_languages": coded(raw.get("spoken_languages"), "iso_639_1", language_name),
            "trailer": trailer(raw, config),
            "directors": crew(credits, lambda e: e.get("job") == "Director"),
            "writers": crew(credits, lambda e: e.get("department") == "Writing"),
            "producers": crew(
                credits,
                lambda e: (
                    e.get("department") == "Production" and "Producer" in (e.get("job") or "")
                ),
            ),
            "actors": cast(credits),
            **image_candidates(raw.get("images") or {}, config),
        }
    )


def collection_candidates(raw: dict[str, Any], config: MovieScrapeConfig) -> dict[str, Any]:
    images = image_candidates(raw.get("images") or {}, config)
    return compact(
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
