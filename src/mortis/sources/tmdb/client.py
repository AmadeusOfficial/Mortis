import time
from collections.abc import Callable
from types import TracebackType
from typing import Any

import httpx2 as httpx

from mortis.config.models import MovieScrapeConfig, TvScrapeConfig
from mortis.config.types import type_config
from mortis.errors import UserError
from mortis.services import Services
from mortis.sources.tmdb.check import TMDB_API, tmdb_auth

IMAGE_BASE = "https://image.tmdb.org/t/p"
MAX_RETRY_AFTER = 10.0


class TmdbError(UserError):
    def __init__(self, message: str, *, status_code: int = 502) -> None:
        super().__init__(message, status_code=status_code)


class TmdbNoKey(TmdbError):
    def __init__(self) -> None:
        super().__init__("还没有填 TMDB API Key", status_code=400)


class TmdbAuthError(TmdbError):
    def __init__(self) -> None:
        super().__init__("TMDB 拒绝了这个 API Key")


class TmdbNotFound(TmdbError):
    def __init__(self) -> None:
        super().__init__("TMDB 上没有这个条目", status_code=404)


class TmdbClient:
    def __init__(
        self,
        http: httpx.Client,
        api_key: str,
        *,
        language: str,
        include_adult: bool,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.http = http
        self.language = language
        self.include_adult = include_adult
        self._key = api_key.strip()
        self._sleep = sleep

    def __enter__(self) -> TmdbClient:
        return self

    def __exit__(
        self,
        _type: type[BaseException] | None,
        _value: BaseException | None,
        _tb: TracebackType | None,
    ) -> None:
        self.http.close()

    def _redact(self, text: str) -> str:
        return text.replace(self._key, "***") if self._key else text

    def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        headers, auth = tmdb_auth(self._key)
        query = {**auth, **(params or {})}
        response: httpx.Response | None = None
        for attempt in range(3):
            try:
                response = self.http.get(f"{TMDB_API}{path}", headers=headers, params=query)
            except httpx.HTTPError as error:
                text = self._redact(str(error) or type(error).__name__)
                raise TmdbError(f"连不上 TMDB：{text}") from None
            if response.status_code != 429 or attempt == 2:
                break
            try:
                wait = float(response.headers.get("Retry-After", "1"))
            except ValueError:
                wait = 1.0
            self._sleep(min(max(wait, 0.0), MAX_RETRY_AFTER))
        assert response is not None
        if response.status_code == 401:
            raise TmdbAuthError
        if response.status_code == 404:
            raise TmdbNotFound
        if response.status_code == 429:
            raise TmdbError("TMDB 请求太频繁，稍后再试")
        if not response.is_success:
            raise TmdbError(f"TMDB 返回 HTTP {response.status_code}")
        try:
            return response.json()
        except ValueError:
            raise TmdbError("TMDB 返回的不是 JSON") from None

    @staticmethod
    def image_url(file_path: str, size: str) -> str:
        return f"{IMAGE_BASE}/{size}{file_path}"


def open_tmdb(
    services: Services, data_type_id: str = "movie", http: httpx.Client | None = None
) -> TmdbClient:
    """按调用方的数据类型取搜索语言和成人内容开关；内置三种各有自己的一节。

    传入 http 时沿用调用方的客户端，这时不要把返回值当上下文管理器用，免得关掉它。
    """
    config = services.config_store.load_config()
    secrets = services.config_store.load_secrets()
    if not secrets.tmdb_api_key.strip():
        raise TmdbNoKey
    section = type_config(config, data_type_id)
    if not isinstance(section, MovieScrapeConfig | TvScrapeConfig):
        section = config.types.movie
    return TmdbClient(
        http if http is not None else services.http_client(),
        secrets.tmdb_api_key,
        language=section.language,
        include_adult=section.include_adult,
    )
