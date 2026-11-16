import re

import httpx2 as httpx
from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel

from mortis.api.deps import ServicesDep
from mortis.sources.tmdb.client import IMAGE_BASE, open_tmdb
from mortis.sources.tmdb.search import MovieHit, search_movies, search_tv

router = APIRouter(prefix="/api/tmdb")

IMAGE_SIZES = frozenset(
    {"w92", "w154", "w185", "w300", "w342", "w500", "w780", "w1280", "original"}
)
_IMAGE_NAME = re.compile(r"[A-Za-z0-9_-]+\.(?:jpg|jpeg|png|webp|svg)")


class MovieHitOut(BaseModel):
    tmdb_id: int
    title: str
    original_title: str
    release_date: str | None
    year: int | None
    overview: str
    poster_url: str | None


def proxy_url(file_path: str | None, size: str = "w185") -> str | None:
    return f"/api/tmdb/image/{size}{file_path}" if file_path else None


@router.get("/search/movie", response_model=list[MovieHitOut])
def search_movie(
    services: ServicesDep,
    query: str = Query(min_length=1, max_length=200),
    year: int | None = Query(default=None, ge=1870, le=2100),
) -> list[MovieHitOut]:
    cleaned = query.strip()
    if not cleaned:
        raise HTTPException(status_code=422, detail="搜索词不能为空")
    with open_tmdb(services) as tmdb:
        return _hits(search_movies(tmdb, cleaned, year))


def _hits(hits: list[MovieHit]) -> list[MovieHitOut]:
    return [
        MovieHitOut(
            tmdb_id=hit.tmdb_id,
            title=hit.title,
            original_title=hit.original_title,
            release_date=hit.release_date,
            year=hit.year,
            overview=hit.overview,
            poster_url=proxy_url(hit.poster_path),
        )
        for hit in hits
    ]


@router.get("/search/tv", response_model=list[MovieHitOut])
def search_show(
    services: ServicesDep,
    query: str = Query(min_length=1, max_length=200),
    year: int | None = Query(default=None, ge=1900, le=2100),
) -> list[MovieHitOut]:
    cleaned = query.strip()
    if not cleaned:
        raise HTTPException(status_code=422, detail="搜索词不能为空")
    with open_tmdb(services, "tv") as tmdb:
        return _hits(search_tv(tmdb, cleaned, year))


@router.get("/image/{size}/{name}")
def tmdb_image(size: str, name: str, services: ServicesDep) -> Response:
    if size not in IMAGE_SIZES or not _IMAGE_NAME.fullmatch(name):
        raise HTTPException(status_code=404, detail="没有这张图")
    with services.http_client() as http:
        try:
            response = http.get(f"{IMAGE_BASE}/{size}/{name}")
        except httpx.HTTPError:
            raise HTTPException(status_code=502, detail="取不到 TMDB 图片") from None
    media_type = response.headers.get("content-type", "")
    if response.status_code != 200 or not media_type.startswith("image/"):
        raise HTTPException(status_code=502, detail="取不到 TMDB 图片")
    return Response(
        content=response.content,
        media_type=media_type,
        headers={"Cache-Control": "public, max-age=86400"},
    )
