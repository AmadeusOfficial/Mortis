"""Fanart.tv 图片来源：电影按 TMDB（或 IMDb）编号，电视剧和动漫按 TVDB 编号。"""

from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING, Any

import httpx2 as httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from mortis.config.models import AppConfig, Secrets, TvScrapeConfig
from mortis.errors import UserError

if TYPE_CHECKING:
    # builtin.py 要登记这里的 FETCHER，运行时反过来 import 会循环。
    from mortis.sources.builtin import FetchRequest

SOURCE = "fanart"
API = "https://webservice.fanart.tv/v3"
TEXTLESS = frozenset({"00", ""})

SIZES: dict[str, tuple[int, int]] = {
    "movieposter": (1000, 1426),
    "moviebackground": (1920, 1080),
    "moviebanner": (1000, 185),
    "hdmovielogo": (800, 310),
    "movielogo": (400, 155),
    "hdmovieclearart": (1000, 562),
    "movieart": (500, 281),
    "moviedisc": (1000, 1000),
    "moviethumb": (1000, 562),
    "tvposter": (1000, 1426),
    "showbackground": (1920, 1080),
    "tvbanner": (1000, 185),
    "hdtvlogo": (800, 310),
    "clearlogo": (400, 155),
    "hdclearart": (1000, 562),
    "clearart": (500, 281),
    "tvthumb": (500, 281),
    "characterart": (512, 512),
    "seasonposter": (1000, 1426),
    "seasonbanner": (1000, 185),
    "seasonthumb": (500, 281),
}
"""fanart.tv 各类图片的规格；接口本身不返回宽高。"""

MOVIE_FIELDS: dict[str, tuple[str, ...]] = {
    "poster": ("movieposter",),
    "fanart": ("moviebackground",),
    "banner": ("moviebanner",),
    "clearlogo": ("hdmovielogo", "movielogo"),
    "clearart": ("hdmovieclearart", "movieart"),
    "discart": ("moviedisc",),
    "thumb": ("moviethumb",),
}
TV_FIELDS: dict[str, tuple[str, ...]] = {
    "poster": ("tvposter",),
    "fanart": ("showbackground",),
    "banner": ("tvbanner",),
    "clearlogo": ("hdtvlogo", "clearlogo"),
    "clearart": ("hdclearart", "clearart"),
    "thumb": ("tvthumb",),
    "characterart": ("characterart",),
}
SEASON_FIELDS: dict[str, str] = {
    "poster": "seasonposter",
    "banner": "seasonbanner",
    "thumb": "seasonthumb",
}
BACKGROUNDS = frozenset({"fanart"})


class Settings(BaseModel):
    """语言取类型设置里的 language，这里没有自己的设置。"""

    model_config = ConfigDict(extra="ignore")


class SecretSettings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    api_key: str = ""
    """项目 key，必填。"""
    client_key: str = ""
    """个人 key，选填。"""


def settings_of(config: AppConfig) -> Settings:
    try:
        return Settings.model_validate(config.sources.get(SOURCE, {}))
    except ValidationError:
        return Settings()


def secrets_of(secrets: Secrets) -> SecretSettings:
    try:
        return SecretSettings.model_validate(secrets.sources.get(SOURCE, {}))
    except ValidationError:
        return SecretSettings()


def check(_config: AppConfig, secrets: Secrets) -> str | None:
    return None if secrets_of(secrets).api_key.strip() else "Key 未填"


class FanartError(UserError):
    def __init__(self, message: str) -> None:
        super().__init__(message, status_code=502)


def _language(value: str | None) -> str | None:
    return None if value is None or value in TEXTLESS else value


def _rank(language: str, textless_first: bool) -> Callable[[dict[str, Any]], int]:
    def rank(entry: dict[str, Any]) -> int:
        value = _language(entry.get("lang"))
        if value is None:
            return 0 if textless_first else 1
        if value == language:
            return 1 if textless_first else 0
        return 2 if value == "en" else 3

    return rank


def _likes(entry: dict[str, Any]) -> int:
    try:
        return int(entry.get("likes") or 0)
    except TypeError, ValueError:
        return 0


def _image(kind: str, entry: dict[str, Any]) -> dict[str, Any]:
    width, height = SIZES.get(kind, (None, None))
    return {
        "url": entry["url"],
        "language": _language(entry.get("lang")),
        "width": width,
        "height": height,
    }


def _ordered(
    raw: dict[str, Any],
    kinds: Iterable[str],
    language: str,
    *,
    textless_first: bool = False,
    keep: Callable[[dict[str, Any]], bool] | None = None,
) -> list[dict[str, Any]]:
    """把几类图片按 语言档 → 列出的类别先后（如先 HD 后普通）→ 点赞 排好。"""
    rank = _rank(language, textless_first)
    entries = [
        (rank(entry), tier, -_likes(entry), kind, entry)
        for tier, kind in enumerate(kinds)
        for entry in raw.get(kind) or []
        if isinstance(entry, dict) and entry.get("url") and (keep is None or keep(entry))
    ]
    entries.sort(key=lambda row: row[:3])
    return [_image(kind, entry) for *_, kind, entry in entries]


def _language_code(language: str) -> str:
    return language.split("-")[0].lower()


def _compact(values: dict[str, list[dict[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
    return {key: value for key, value in values.items() if value}


def movie_candidates(raw: dict[str, Any], language: str, textless_fanart: bool) -> dict[str, Any]:
    code = _language_code(language)
    values = {
        field_key: _ordered(
            raw, kinds, code, textless_first=textless_fanart and field_key in BACKGROUNDS
        )
        for field_key, kinds in MOVIE_FIELDS.items()
    }
    values["extrafanart"] = values["fanart"][1:]
    return _compact(values)


def show_candidates(raw: dict[str, Any], language: str, textless_fanart: bool) -> dict[str, Any]:
    code = _language_code(language)
    return _compact(
        {
            field_key: _ordered(
                raw, kinds, code, textless_first=textless_fanart and field_key in BACKGROUNDS
            )
            for field_key, kinds in TV_FIELDS.items()
        }
    )


def season_candidates(
    raw: dict[str, Any], season_number: int, language: str
) -> dict[str, list[dict[str, Any]]]:
    """一季的图片候选值，键是季的字段键。先列这一季的，再列 season 为 all 的通用图。"""
    code = _language_code(language)
    wanted = str(season_number)
    values: dict[str, list[dict[str, Any]]] = {}
    for field_key, kind in SEASON_FIELDS.items():
        values[field_key] = _ordered(
            raw, (kind,), code, keep=lambda entry: str(entry.get("season")) == wanted
        ) + _ordered(raw, (kind,), code, keep=lambda entry: entry.get("season") == "all")
    return _compact(values)


class _FanartFetcher:
    def fetch(self, request: FetchRequest) -> dict[str, Any] | None:
        keys = secrets_of(request.services.config_store.load_secrets())
        if isinstance(request.config, TvScrapeConfig):
            tvdb_id = request.ids.get("tvdb")
            if not tvdb_id:
                request.log("没有 TVDB 编号，跳过 Fanart.tv")
                return None
            request.log(f"请求 Fanart.tv 剧 {tvdb_id}")
            return get(request.http, f"/tv/{tvdb_id}", keys)
        movie_id = request.ids.get("tmdb") or request.ids.get("imdb")
        if not movie_id:
            request.log("没有 TMDB 或 IMDb 编号，跳过 Fanart.tv")
            return None
        request.log(f"请求 Fanart.tv 电影 {movie_id}")
        return get(request.http, f"/movies/{movie_id}", keys)

    def candidates(self, raw: dict[str, Any], request: FetchRequest) -> dict[str, Any]:
        config = request.config
        if isinstance(config, TvScrapeConfig):
            return show_candidates(raw, config.language, config.prefer_textless_fanart)
        return movie_candidates(raw, config.language, config.prefer_textless_fanart)


def get(http: httpx.Client, path: str, keys: SecretSettings) -> dict[str, Any] | None:
    """请求 v3 接口；没找到时返回 None。key 只放在查询参数里，不进返回值和异常消息。"""
    api_key = keys.api_key.strip()
    client_key = keys.client_key.strip()
    params = {"api_key": api_key}
    if client_key:
        params["client_key"] = client_key

    def redact(text: str) -> str:
        for secret in (api_key, client_key):
            if secret:
                text = text.replace(secret, "***")
        return text

    try:
        response = http.get(f"{API}{path}", params=params)
    except httpx.HTTPError as error:
        raise FanartError(
            f"连不上 Fanart.tv：{redact(str(error) or type(error).__name__)}"
        ) from None
    if response.status_code == 404:
        return None
    if response.status_code in (401, 403):
        raise FanartError("Fanart.tv 拒绝了这个 Key")
    if not response.is_success:
        raise FanartError(f"Fanart.tv 返回 HTTP {response.status_code}")
    try:
        body = response.json()
    except ValueError:
        raise FanartError("Fanart.tv 返回的不是 JSON") from None
    if not isinstance(body, dict):
        raise FanartError("Fanart.tv 返回的不是 JSON 对象")
    if body.get("status") == "error":
        return None
    return body


FETCHER = _FanartFetcher()
