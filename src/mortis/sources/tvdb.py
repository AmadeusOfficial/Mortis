"""TVDB v4：剧级文本和图片，季图片，以及按播出顺序对上已有的集时补集标题和剧情。

只读不建：季集结构以 TMDB 为准，这里不创建季和集。
"""

import threading
from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING, Any

import httpx2 as httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from mortis.config.models import AppConfig, Secrets
from mortis.errors import UserError

if TYPE_CHECKING:
    # builtin.py 要导入本模块登记来源，运行时导入会循环。
    from mortis.sources.builtin import FetchRequest

SOURCE = "tvdb"
API = "https://api4.thetvdb.com/v4"
ARTWORK_BASE = "https://artworks.thetvdb.com"
MAX_EPISODE_PAGES = 50
EMPTY: tuple[Any, ...] = (None, "", [], {})

# TVDB 的图片类型编号。
SERIES_ARTWORK = {"banner": 1, "poster": 2, "fanart": 3, "clearlogo": 23}
SEASON_ARTWORK = {"banner": 6, "poster": 7, "fanart": 8}
AIRED_ORDER = "official"

# zh-TW、zh-HK 对应繁体（zhtw），简体只取 zho，不互相回退。
REGION_LANGUAGES = {
    "zh-CN": "zho",
    "zh-SG": "zho",
    "zh-TW": "zhtw",
    "zh-HK": "zhtw",
}
LANGUAGES = {
    "zh": "zho",
    "en": "eng",
    "ja": "jpn",
    "ko": "kor",
    "fr": "fra",
    "de": "deu",
    "es": "spa",
    "it": "ita",
    "ru": "rus",
    "pt": "por",
    "nl": "nld",
    "sv": "swe",
    "da": "dan",
    "fi": "fin",
    "no": "nor",
    "pl": "pol",
    "cs": "ces",
    "hu": "hun",
    "el": "ell",
    "tr": "tur",
    "he": "heb",
    "ar": "ara",
    "th": "tha",
    "vi": "vie",
}
SHORT_LANGUAGES = {code: short for short, code in LANGUAGES.items()} | {"zhtw": "zh"}

STATUS = {
    "Continuing": "连载中",
    "Ended": "已完结",
    "Upcoming": "计划中",
}

# 名字对齐 TMDB 中文类型名；TMDB 没有的类型给常用译名。
GENRES = {
    "Action": "动作",
    "Adventure": "冒险",
    "Animation": "动画",
    "Anime": "动画",
    "Children": "儿童",
    "Comedy": "喜剧",
    "Crime": "犯罪",
    "Documentary": "纪录",
    "Drama": "剧情",
    "Family": "家庭",
    "Fantasy": "奇幻",
    "Food": "美食",
    "Game Show": "游戏节目",
    "History": "历史",
    "Home and Garden": "家居园艺",
    "Horror": "恐怖",
    "Indie": "独立",
    "Martial Arts": "武术",
    "Mini-Series": "迷你剧",
    "Musical": "音乐",
    "Mystery": "悬疑",
    "News": "新闻",
    "Podcast": "播客",
    "Reality": "真人秀",
    "Romance": "爱情",
    "Science Fiction": "科幻",
    "Soap": "肥皂剧",
    "Sport": "体育",
    "Suspense": "悬疑",
    "Talk Show": "脱口秀",
    "Thriller": "惊悚",
    "Travel": "旅行",
    "War": "战争",
    "Western": "西部",
    "Awards Show": "颁奖典礼",
    "Sitcom": "情景喜剧",
}

NETWORK = "Network"
STUDIOS = ("Production Company", "Studio")


class Settings(BaseModel):
    model_config = ConfigDict(extra="ignore")


class SecretSettings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    api_key: str = ""
    pin: str = ""


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


class TvdbError(UserError):
    def __init__(self, message: str) -> None:
        super().__init__(message, status_code=502)


class TvdbAuthError(TvdbError):
    def __init__(self) -> None:
        super().__init__("TVDB 拒绝了这个 Key")


class _NotFound(Exception):
    pass


_token_guard = threading.Lock()
_tokens: dict[tuple[str, str], str] = {}
"""登录令牌按 (apikey, pin) 缓存在进程内，不落盘。"""


def tvdb_language(language: str) -> str:
    """类型设置里的语言（如 zh-CN）换成 TVDB 的三字母代码。"""
    if language in REGION_LANGUAGES:
        return REGION_LANGUAGES[language]
    short = language.split("-")[0]
    return LANGUAGES.get(short, short)


class _Client:
    def __init__(self, http: httpx.Client, api_key: str, pin: str) -> None:
        self.http = http
        self._cache_key = (api_key, pin)

    def _login(self) -> str:
        api_key, pin = self._cache_key
        body = {"apikey": api_key, **({"pin": pin} if pin else {})}
        response = self._send("POST", "/login", json=body)
        if response.status_code == 401:
            raise TvdbAuthError
        if not response.is_success:
            raise TvdbError(f"TVDB 登录失败：HTTP {response.status_code}")
        token = (self._json(response).get("data") or {}).get("token")
        if not token:
            raise TvdbError("TVDB 登录没有返回令牌")
        return token

    def _token(self, *, renew: bool = False) -> str:
        with _token_guard:
            token = None if renew else _tokens.get(self._cache_key)
            if token is None:
                token = self._login()
                _tokens[self._cache_key] = token
            return token

    def _send(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            return self.http.request(method, f"{API}{path}", **kwargs)
        except httpx.HTTPError as error:
            raise TvdbError(f"连不上 TVDB：{type(error).__name__}") from None

    @staticmethod
    def _json(response: httpx.Response) -> dict[str, Any]:
        try:
            body = response.json()
        except ValueError:
            raise TvdbError("TVDB 返回的不是 JSON") from None
        if not isinstance(body, dict):
            raise TvdbError("TVDB 返回的不是 JSON 对象")
        return body

    def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """令牌过期（401）时重登一次再试；还是 401 就当 Key 被拒。"""
        response: httpx.Response | None = None
        for attempt in range(2):
            token = self._token(renew=attempt > 0)
            response = self._send(
                "GET", path, params=params, headers={"Authorization": f"Bearer {token}"}
            )
            if response.status_code != 401:
                break
        assert response is not None
        if response.status_code == 401:
            with _token_guard:
                _tokens.pop(self._cache_key, None)
            raise TvdbAuthError
        if response.status_code == 404:
            raise _NotFound
        if not response.is_success:
            raise TvdbError(f"TVDB 返回 HTTP {response.status_code}")
        return self._json(response)


def _data(body: dict[str, Any] | None) -> dict[str, Any]:
    data = (body or {}).get("data")
    return data if isinstance(data, dict) else {}


class _TvdbFetcher:
    def fetch(self, request: FetchRequest) -> dict[str, Any] | None:
        tvdb_id = (request.ids.get(SOURCE) or "").strip()
        if not tvdb_id:
            request.log("TVDB：条目没有 TVDB 编号，跳过")
            return None
        keys = secrets_of(request.services.config_store.load_secrets())
        client = _Client(request.http, keys.api_key.strip(), keys.pin.strip())
        language = tvdb_language(request.config.language)
        request.log(f"请求 TVDB 剧 {tvdb_id}")
        try:
            series = client.get(f"/series/{tvdb_id}/extended", {"short": "false"})
        except _NotFound:
            return None
        translation: dict[str, Any] | None = None
        if language in _translated(_data(series)):
            request.log(f"请求 TVDB 剧 {tvdb_id} 的翻译（{language}）")
            try:
                translation = client.get(f"/series/{tvdb_id}/translations/{language}")
            except _NotFound:
                translation = None
        episodes: list[dict[str, Any]] = []
        for page in range(MAX_EPISODE_PAGES):
            request.log(f"请求 TVDB 剧 {tvdb_id} 的集（第 {page + 1} 页）")
            try:
                body = client.get(f"/series/{tvdb_id}/episodes/{AIRED_ORDER}", {"page": page})
            except _NotFound:
                break
            episodes.append(body)
            if not (body.get("links") or {}).get("next"):
                break
        return {
            "language": language,
            "series": series,
            "translation": translation,
            "episodes": episodes,
        }

    def candidates(self, raw: dict[str, Any], request: FetchRequest) -> dict[str, Any]:
        series = _data(raw.get("series"))
        language = raw.get("language") or tvdb_language(request.config.language)
        title, overview = _texts(raw)
        normal = _ranker([language, "eng", None])
        textless = _ranker([None, language, "eng"])
        fanart_rank = textless if request.config.prefer_textless_fanart else normal
        artworks = series.get("artworks") or []
        images = {
            field: _images(
                (a for a in artworks if a.get("type") == kind),
                fanart_rank if field == "fanart" else normal,
            )
            for field, kind in SERIES_ARTWORK.items()
        }
        status = (series.get("status") or {}).get("name")
        chinese = language in ("zho", "zhtw")
        return _compact(
            {
                "title": title,
                "original_title": series.get("name"),
                "overview": overview,
                "status": STATUS.get(status or "", status) if chinese else status,
                "release_date": series.get("firstAired"),
                "genres": _genres(series.get("genres"), chinese),
                "studios": _studios(series),
                **images,
            }
        )


def _translated(series: dict[str, Any]) -> set[str]:
    return {*(series.get("nameTranslations") or []), *(series.get("overviewTranslations") or [])}


def _texts(raw: dict[str, Any]) -> tuple[str | None, str | None]:
    """要的语言没有翻译时，只有原语言正好是它才用原文；否则留空让别的来源补。"""
    translation = _data(raw.get("translation"))
    if translation:
        return translation.get("name") or None, translation.get("overview") or None
    series = _data(raw.get("series"))
    if series.get("originalLanguage") == raw.get("language"):
        return series.get("name") or None, series.get("overview") or None
    return None, None


def _ranker(order: list[str | None]) -> Callable[[str | None], int]:
    positions = {language: index for index, language in enumerate(order)}
    return lambda language: positions.get(language, len(order))


def _image_url(path: str) -> str:
    return path if path.startswith("http") else f"{ARTWORK_BASE}{path}"


def _images(
    entries: Iterable[dict[str, Any]], rank: Callable[[str | None], int]
) -> list[dict[str, Any]]:
    ordered = sorted(
        (entry for entry in entries if entry.get("image")),
        key=lambda entry: (
            rank(entry.get("language") or None),
            -(entry.get("score") or 0),
            -(entry.get("width") or 0),
        ),
    )
    return [
        {
            "url": _image_url(entry["image"]),
            "language": SHORT_LANGUAGES.get(entry.get("language") or "", entry.get("language"))
            or None,
            "width": entry.get("width"),
            "height": entry.get("height"),
        }
        for entry in ordered
    ]


def _genres(entries: list[dict[str, Any]] | None, chinese: bool) -> list[str]:
    result: list[str] = []
    for entry in entries or []:
        name = entry.get("name")
        if chinese:
            name = GENRES.get(name or "")
        if name and name not in result:
            result.append(name)
    return result


def _studios(series: dict[str, Any]) -> list[str]:
    companies = series.get("companies") or []

    def of_type(*types: str) -> list[dict[str, Any]]:
        return [
            c for c in companies if (c.get("companyType") or {}).get("companyTypeName") in types
        ]

    ordered = [
        series.get("originalNetwork") or {},
        series.get("latestNetwork") or {},
        *of_type(NETWORK),
        *of_type(*STUDIOS),
    ]
    result: list[str] = []
    for entry in ordered:
        name = entry.get("name")
        if name and name not in result:
            result.append(name)
    return result


def _compact(values: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in values.items() if value not in EMPTY}


def _aired_season(raw: dict[str, Any], season_number: int) -> dict[str, Any] | None:
    for season in _data(raw.get("series")).get("seasons") or []:
        if (season.get("type") or {}).get("type") == AIRED_ORDER and season.get(
            "number"
        ) == season_number:
            return season
    return None


def season_candidates(raw: dict[str, Any], season_number: int) -> dict[str, Any]:
    """季图片，season_number 是播出顺序（aired）的季号。TVDB 上没有这一季时返回空字典。"""
    season = _aired_season(raw, season_number)
    if season is None:
        return {}
    language = raw.get("language")
    rank = _ranker([language, "eng", None])
    artworks = [
        a
        for a in _data(raw.get("series")).get("artworks") or []
        if a.get("seasonId") is not None and a.get("seasonId") == season.get("id")
    ]
    values = {
        field: _images((a for a in artworks if a.get("type") == kind), rank)
        for field, kind in SEASON_ARTWORK.items()
    }
    if not values["poster"] and season.get("image"):
        values["poster"] = _images([{"image": season["image"]}], rank)
    return _compact(values)


def episode_candidates(
    raw: dict[str, Any], season_number: int, episode_number: int
) -> dict[str, Any]:
    """按播出顺序的季号和集号对上时给集标题和剧情；对不上返回空字典。

    集列表里的名字是剧原语言的，所以只有原语言正好是要的语言时才给，否则留空让别的来源补。
    """
    series = _data(raw.get("series"))
    if series.get("originalLanguage") != raw.get("language"):
        return {}
    for page in raw.get("episodes") or []:
        for episode in _data(page).get("episodes") or []:
            if (
                episode.get("seasonNumber") == season_number
                and episode.get("number") == episode_number
            ):
                return _compact({"title": episode.get("name"), "overview": episode.get("overview")})
    return {}


FETCHER = _TvdbFetcher()
