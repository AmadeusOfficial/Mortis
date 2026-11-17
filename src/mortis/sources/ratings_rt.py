"""烂番茄影评人评分：默认查 Wikidata 的公开 SPARQL，填了 MDBList 的 key 就优先用 MDBList。"""

import re
from typing import TYPE_CHECKING, Any

import httpx2 as httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from mortis.config.models import AppConfig, Secrets, TvScrapeConfig
from mortis.errors import UserError
from mortis.version import get_version

if TYPE_CHECKING:
    # builtin.py 接线时会导入本模块，运行时导入会循环。
    from mortis.sources.builtin import FetchRequest

SOURCE_ID = "ratings_rt"
RATING_SOURCE = "tomatometerallcritics"
FIELD_KEY = "ratings"

WIKIDATA_URL = "https://query.wikidata.org/sparql"
MDBLIST_URL = "https://api.mdblist.com/imdb/{kind}/{imdb_id}"

# Wikidata 的属性和条目编号。
IMDB_ID = "P345"
REVIEW_SCORE = "P444"
REVIEW_SCORE_BY = "P447"
DETERMINATION_METHOD = "P459"
NUMBER_OF_REVIEWS = "P7887"
POINT_IN_TIME = "P585"
ROTTEN_TOMATOES = "Q105584"
TOMATOMETER = "Q108403393"
ENTITY_PREFIX = "http://www.wikidata.org/entity/"

IMDB_PATTERN = re.compile(r"tt\d+")
PERCENT_PATTERN = re.compile(r"\s*(\d+(?:\.\d+)?)\s*%\s*")


class Settings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    enabled: bool = True


class SecretSettings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    mdblist_api_key: str = ""


def settings_of(config: AppConfig) -> Settings:
    try:
        return Settings.model_validate(config.sources.get(SOURCE_ID, {}))
    except ValidationError:
        return Settings()


def secrets_of(secrets: Secrets) -> SecretSettings:
    try:
        return SecretSettings.model_validate(secrets.sources.get(SOURCE_ID, {}))
    except ValidationError:
        return SecretSettings()


def check(config: AppConfig, _secrets: Secrets) -> str | None:
    return None if settings_of(config).enabled else "已关闭"


def provider_name(secrets: Secrets) -> str:
    """设置页显示这次会用哪个服务。"""
    return "MDBList" if secrets_of(secrets).mdblist_api_key.strip() else "Wikidata"


def user_agent() -> str:
    # Wikidata 的查询服务要求说明性的 User-Agent，否则可能被限流或拒绝；请求头只能是 ASCII。
    return f"Mortis/{get_version()} (personal local media library)"


def sparql_query(imdb_id: str) -> str:
    return f"""SELECT ?item ?value ?method ?count ?date WHERE {{
  ?item wdt:{IMDB_ID} "{imdb_id}" .
  ?item p:{REVIEW_SCORE} ?statement .
  ?statement ps:{REVIEW_SCORE} ?value ;
             pq:{REVIEW_SCORE_BY} wd:{ROTTEN_TOMATOES} .
  OPTIONAL {{ ?statement pq:{DETERMINATION_METHOD} ?method . }}
  OPTIONAL {{ ?statement pq:{NUMBER_OF_REVIEWS} ?count . }}
  OPTIONAL {{ ?statement pq:{POINT_IN_TIME} ?date . }}
}}"""


def _percent(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        number = float(value)
    elif isinstance(value, str):
        match = PERCENT_PATTERN.fullmatch(value) or re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*", value)
        if match is None:
            return None
        number = float(match.group(1))
    else:
        return None
    return round(number) if 0 <= number <= 100 else None


def _count(value: Any) -> int | None:
    if isinstance(value, bool) or value in (None, ""):
        return None
    try:
        count = int(float(str(value).lstrip("+")))
    except ValueError:
        return None
    return count if count >= 0 else None


def _rating(score: int, votes: int | None) -> dict[str, Any]:
    return {"source": RATING_SOURCE, "rating": score, "max_rating": 100, "vote_count": votes}


def _binding(row: dict[str, Any], name: str) -> str | None:
    cell = row.get(name)
    value = cell.get("value") if isinstance(cell, dict) else None
    return value if isinstance(value, str) else None


def wikidata_rating(response: Any) -> dict[str, Any] | None:
    """SPARQL 结果里的 Tomatometer。只要百分比那条，多条取时间最新的。

    计分方法标成 Tomatometer 的优先；都没标时才用没标方法的百分比，
    标了别的方法（如观众评分）的不要。
    """
    results = response.get("results") if isinstance(response, dict) else None
    rows = results.get("bindings") if isinstance(results, dict) else None
    tagged: list[tuple[str, int, int | None]] = []
    untagged: list[tuple[str, int, int | None]] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        value = _binding(row, "value")
        if value is None or "%" not in value or (score := _percent(value)) is None:
            continue
        entry = ((_binding(row, "date") or "").lstrip("+"), score, _count(_binding(row, "count")))
        method = _binding(row, "method")
        if method is None:
            untagged.append(entry)
        elif method.removeprefix(ENTITY_PREFIX) == TOMATOMETER:
            tagged.append(entry)
    pool = tagged or untagged
    if not pool:
        return None
    _date, score, votes = max(pool, key=lambda entry: entry[0])
    return _rating(score, votes)


def mdblist_rating(response: Any) -> dict[str, Any] | None:
    ratings = response.get("ratings") if isinstance(response, dict) else None
    for entry in ratings if isinstance(ratings, list) else []:
        if not isinstance(entry, dict) or entry.get("source") != "tomatoes":
            continue
        score = _percent(entry.get("value"))
        if score is None:
            score = _percent(entry.get("score"))
        if score is not None:
            return _rating(score, _count(entry.get("votes")))
    return None


def rating_of(raw: dict[str, Any]) -> dict[str, Any] | None:
    response = raw.get("response")
    if raw.get("provider") == "mdblist":
        return mdblist_rating(response)
    return wikidata_rating(response)


def _json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return None


class _RatingsRtFetcher:
    def fetch(self, request: FetchRequest) -> dict[str, Any] | None:
        imdb_id = (request.ids.get("imdb") or "").strip()
        if not IMDB_PATTERN.fullmatch(imdb_id):
            request.log("没有 IMDb 编号，跳过烂番茄评分")
            return None
        key = secrets_of(request.services.config_store.load_secrets()).mdblist_api_key.strip()
        if key:
            raw = self._mdblist(request, imdb_id, key)
            if raw is not None and rating_of(raw) is not None:
                return raw
        raw = self._wikidata(request, imdb_id)
        if rating_of(raw) is None:
            request.log(f"Wikidata 上 {imdb_id} 没有烂番茄影评人评分")
            return None
        return raw

    def _mdblist(self, request: FetchRequest, imdb_id: str, key: str) -> dict[str, Any] | None:
        kind = "show" if isinstance(request.config, TvScrapeConfig) else "movie"
        url = MDBLIST_URL.format(kind=kind, imdb_id=imdb_id)
        request.log(f"请求 MDBList {kind} {imdb_id}")
        try:
            response = request.http.get(url, params={"apikey": key})
        except httpx.HTTPError as error:
            # 异常消息里可能带着含 key 的地址，只记类型。
            request.log(f"MDBList 请求失败（{type(error).__name__}），改用 Wikidata")
            return None
        if response.status_code >= 400:
            request.log(f"MDBList 请求失败（HTTP {response.status_code}），改用 Wikidata")
            return None
        raw = {"provider": "mdblist", "imdb_id": imdb_id, "response": _json(response)}
        if rating_of(raw) is None:
            request.log(f"MDBList 上 {imdb_id} 没有烂番茄评分，改用 Wikidata")
        return raw

    def _wikidata(self, request: FetchRequest, imdb_id: str) -> dict[str, Any]:
        request.log(f"请求 Wikidata 烂番茄评分 {imdb_id}")
        response = request.http.get(
            WIKIDATA_URL,
            params={"format": "json", "query": sparql_query(imdb_id)},
            # 带 Accept: application/sparql-results+json 时 Wikidata 直接回 429，格式只靠 format。
            headers={"User-Agent": user_agent()},
        )
        if response.status_code >= 400:
            raise UserError(f"Wikidata 返回 HTTP {response.status_code}")
        return {"provider": "wikidata", "imdb_id": imdb_id, "response": _json(response)}

    def candidates(self, raw: dict[str, Any], request: FetchRequest) -> dict[str, Any]:
        rating = rating_of(raw)
        return {FIELD_KEY: [rating]} if rating is not None else {}


FETCHER = _RatingsRtFetcher()
