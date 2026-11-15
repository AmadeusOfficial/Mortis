import json
from pathlib import Path
from typing import Any

import httpx2 as httpx

FIXTURES = Path(__file__).parent / "fixtures" / "tmdb"


def load_fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class FakeTmdb:
    """按路径回放 TMDB 接口；图片地址回不同的字节，方便按哈希区分。"""

    def __init__(self) -> None:
        self.routes: dict[str, tuple[int, Any]] = {}
        self.searches: dict[str, list[dict[str, Any]]] = {}
        self.requests: list[httpx.Request] = []
        self.image_status = 200

    def add(self, path: str, body: Any, status: int = 200) -> None:
        self.routes[path] = (status, body)

    def add_search(self, query: str, results: list[dict[str, Any]]) -> None:
        self.searches[query] = results

    def paths(self) -> list[str]:
        return [request.url.path for request in self.requests]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.host == "image.tmdb.org":
            if self.image_status != 200:
                return httpx.Response(self.image_status)
            return httpx.Response(
                200,
                content=b"img:" + request.url.path.encode(),
                headers={"content-type": "image/jpeg"},
            )
        if request.url.path == "/3/search/movie":
            results = self.searches.get(request.url.params.get("query", ""), [])
            year = request.url.params.get("year")
            if year is not None:
                results = [r for r in results if (r.get("release_date") or "").startswith(year)]
            return httpx.Response(200, json={"page": 1, "results": results})
        status, body = self.routes.get(request.url.path, (404, {"status_message": "not found"}))
        return httpx.Response(status, json=body)

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)


def hit(
    tmdb_id: int,
    title: str,
    date: str,
    original: str | None = None,
    poster: str | None = None,
) -> dict[str, Any]:
    return {
        "id": tmdb_id,
        "title": title,
        "original_title": original or title,
        "release_date": date,
        "overview": "",
        "poster_path": poster,
        "popularity": 1.0,
    }
