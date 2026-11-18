import json
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx2 as httpx
import pytest

from mortis.files.trash import remove_permanently

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
        if request.url.path == "/3/search/tv":
            results = self.searches.get(request.url.params.get("query", ""), [])
            year = request.url.params.get("first_air_date_year")
            if year is not None:
                results = [r for r in results if (r.get("first_air_date") or "").startswith(year)]
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


class FakeLauncher:
    """记下宿主要启动的程序和要用默认程序打开的文件，不真的启动任何东西。"""

    def __init__(self) -> None:
        self.launched: list[tuple[str, list[str]]] = []
        self.cwds: list[str | None] = []
        self.opened: list[str] = []
        self.revealed: list[tuple[str, bool]] = []
        self.fail: OSError | None = None

    def launch(self, executable: str, args: list[str], cwd: str | None = None) -> None:
        if self.fail is not None:
            raise self.fail
        self.launched.append((executable, args))
        self.cwds.append(cwd)

    def open_default(self, path: str) -> None:
        if self.fail is not None:
            raise self.fail
        self.opened.append(path)

    def reveal(self, path: str, *, select: bool) -> None:
        if self.fail is not None:
            raise self.fail
        self.revealed.append((path, select))


def make_junction(link: Path, target: Path) -> None:
    """用 mklink /J 建目录联接，建不了就跳过这个测试。"""
    if sys.platform != "win32":
        pytest.skip("只有 Windows 有目录联接")
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
        timeout=30,
        check=False,
    )
    if result.returncode != 0 or not link.exists():
        pytest.skip("建不了目录联接")


class FakeTrash:
    """直接删掉临时目录里的文件，记下（路径，是否永久删除），不碰系统回收站。

    fail 里的路径删除时报错。
    """

    def __init__(self, *, permanent: bool = False) -> None:
        self.permanent = permanent
        self.removed: list[tuple[Path, bool]] = []
        self.fail: set[Path] = set()
        self.before_remove: Callable[[Path], None] | None = None

    def is_permanent(self, path: Path) -> bool:
        return self.permanent

    def remove(self, path: Path) -> None:
        if self.before_remove is not None:
            self.before_remove(path)
        if path in self.fail:
            raise PermissionError(f"文件正被占用：{path}")
        remove_permanently(path)
        self.removed.append((path, self.permanent))


def tv_hit(tmdb_id: int, name: str, date: str, original: str | None = None) -> dict[str, Any]:
    return {
        "id": tmdb_id,
        "name": name,
        "original_name": original or name,
        "first_air_date": date,
        "overview": "",
        "poster_path": None,
        "popularity": 1.0,
    }
