"""月幕 Galgame 来源插件，只填 Galgame 字段。网络请求一律走宿主的 get_json、post_json。

月幕文档把令牌端点写成 `GET /oauth/token`，但表单 POST 同样可用，这里按 OAuth2 的惯例用 POST。
"""

import re
import time
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field

from mortis.plugin_api import Hit, Host, SourceAuthError, SourceError, SourcePlugin

BASE_URL = "https://www.ymgal.games"
TOKEN_URL = f"{BASE_URL}/oauth/token"
SEARCH_URL = f"{BASE_URL}/open/archive/search-game"
ARCHIVE_URL = f"{BASE_URL}/open/archive"

# 月幕文档「Public client identifier」一节的公用客户端，仅供非商业接入。
PUBLIC_CLIENT_ID = "ymgal"
PUBLIC_CLIENT_SECRET = "luna0327"

# 令牌过期前这么多秒就当它过期，避免在请求途中失效。
TOKEN_MARGIN = 60.0
DEFAULT_EXPIRES_IN = 3600.0

AUTH_CODES = (401, 403)
SEARCH_PAGE_SIZE = 10
SCENARIO_KEYWORDS = ("剧本", "脚本")
ARTIST_KEYWORDS = ("原画",)

_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


class Settings(BaseModel):
    """默认填公用客户端；换了专属客户端就覆盖这两项。"""

    model_config = ConfigDict(extra="ignore")

    client_id: str = Field(
        PUBLIC_CLIENT_ID, title="client_id", description="默认是月幕的公用客户端"
    )
    client_secret: str = Field(PUBLIC_CLIENT_SECRET, title="client_secret")


def _text(value: Any) -> str | None:
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return None


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _as_mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _mapping_entry(mapping: dict[str, Any], key: Any) -> dict[str, Any]:
    """cidMapping、pidMapping 的键是字符串编号。"""
    if key is None:
        return {}
    return _as_mapping(mapping.get(str(key)))


def _year(value: Any) -> int | None:
    text = _text(value) or ""
    if len(text) >= 4 and text[:4].isdigit():
        return int(text[:4])
    return None


def _full_date(value: Any) -> str | None:
    text = _text(value) or ""
    return text if _DATE.fullmatch(text) else None


def _images(url: str | None) -> list[dict[str, Any]]:
    if not url:
        return []
    return [{"url": url, "language": None, "width": None, "height": None}]


def _first_link(rows: Any) -> str | None:
    for row in _as_list(rows):
        if isinstance(row, dict):
            link = _text(row.get("link"))
            if link:
                return link
    return None


def _person(entry: dict[str, Any], external_id: str, preferred: str | None) -> dict[str, Any]:
    original = _text(entry.get("name"))
    name = _text(preferred) or original or ""
    return {
        "external_id": external_id,
        "name": name,
        "original_name": original if original and original != name else None,
        "character": None,
        "photo_url": _text(entry.get("mainImg")),
    }


def _voice_actors(
    characters: Any, cid_mapping: dict[str, Any], pid_mapping: dict[str, Any]
) -> list[dict[str, Any]]:
    """一个声优配多个角色时并成一条，角色名用「 / 」连接。"""
    persons: dict[str, dict[str, Any]] = {}
    roles: dict[str, list[str]] = {}
    for row in _as_list(characters):
        if not isinstance(row, dict):
            continue
        cv_id = row.get("cvId")
        if not cv_id:
            continue
        external_id = str(cv_id)
        if external_id not in persons:
            entry = _mapping_entry(pid_mapping, cv_id)
            chinese = _text(entry.get("chineseName"))
            persons[external_id] = _person(entry, external_id, chinese)
            roles[external_id] = []
        role = _mapping_entry(cid_mapping, row.get("cid"))
        role_name = _text(role.get("chineseName")) or _text(role.get("name"))
        if role_name and role_name not in roles[external_id]:
            roles[external_id].append(role_name)

    result: list[dict[str, Any]] = []
    for external_id, person in persons.items():
        person["character"] = " / ".join(roles[external_id]) or None
        result.append(person)
    return result


def _staff(
    staff: Any, pid_mapping: dict[str, Any], keywords: tuple[str, ...]
) -> list[dict[str, Any]]:
    """同一个人可能挂了多个职位，按 pid 去重；没有 pid 的人退回用 sid 当编号。"""
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in _as_list(staff):
        if not isinstance(row, dict):
            continue
        job = _text(row.get("jobName")) or ""
        if not any(word in job for word in keywords):
            continue
        pid = row.get("pid")
        sid = row.get("sid")
        if pid:
            external_id = str(pid)
        elif sid:
            external_id = str(sid)
        else:
            continue
        if external_id in seen:
            continue
        seen.add(external_id)
        entry = _mapping_entry(pid_mapping, pid)
        chinese = _text(entry.get("chineseName"))
        person = _person(entry, external_id, chinese or _text(row.get("empName")))
        if not person["name"]:
            continue
        result.append(person)
    return result


def _drop_empty(candidates: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in candidates.items() if value not in (None, "", [])}


class YmgalSource(SourcePlugin):
    id: ClassVar[str] = "ymgal"
    name: ClassVar[str] = "月幕 Galgame"
    data_type: ClassVar[str] = "galgame"
    provider: ClassVar[str] = "ymgal"
    Settings = Settings
    requests_per_second: ClassVar[float] = 1.0

    def __init__(self) -> None:
        super().__init__()
        self._access_token: str | None = None
        self._expires_at = 0.0

    def enabled(self, host: Host) -> bool:
        return bool(self._client_id(host) and self._client_secret(host))

    def disabled_reason(self, host: Host) -> str | None:
        if self.enabled(host):
            return None
        return "没填月幕的 client_id 和 client_secret，需要到 ymgal.games/developer 申请后填入"

    def search(self, host: Host, title: str) -> list[Hit]:
        data = self._get_data(
            host,
            SEARCH_URL,
            {"mode": "list", "keyword": title, "pageNum": 1, "pageSize": SEARCH_PAGE_SIZE},
        )
        hits: list[Hit] = []
        for row in _as_list(data.get("result")):
            if not isinstance(row, dict) or row.get("id") is None:
                continue
            name = _text(row.get("name"))
            display = _text(row.get("chineseName")) or name
            if display is None:
                continue
            hits.append(
                Hit(
                    external_id=str(row["id"]),
                    title=display,
                    original_title=name,
                    year=_year(row.get("releaseDate")),
                    poster_url=_text(row.get("mainImg")),
                )
            )
        host.log(f"月幕搜索「{title}」拿到 {len(hits)} 条")
        return hits

    def fetch(self, host: Host, external_id: str) -> dict[str, Any]:
        data = self._get_data(host, ARCHIVE_URL, {"gid": external_id})
        game = data.get("game")
        if not isinstance(game, dict):
            raise SourceError(f"月幕条目 {external_id} 没有游戏档案")
        data["developer"] = self._developer_name(host, game)
        return data

    def candidates(self, raw: dict[str, Any]) -> dict[str, Any]:
        game = _as_mapping(raw.get("game"))
        if not game:
            return {}
        cid_mapping = _as_mapping(raw.get("cidMapping"))
        pid_mapping = _as_mapping(raw.get("pidMapping"))
        developer = _text(raw.get("developer"))
        values: dict[str, Any] = {
            "title": _text(game.get("chineseName")) or _text(game.get("name")),
            "original_title": _text(game.get("name")),
            "overview": _text(game.get("introduction")),
            "release_date": _full_date(game.get("releaseDate")),
            "poster": _images(_text(game.get("mainImg"))),
            "brands": [developer] if developer else [],
            "website": _first_link(game.get("website")),
            "voice_actors": _voice_actors(game.get("characters"), cid_mapping, pid_mapping),
            "scenario": _staff(game.get("staff"), pid_mapping, SCENARIO_KEYWORDS),
            "artists": _staff(game.get("staff"), pid_mapping, ARTIST_KEYWORDS),
        }
        return _drop_empty(values)

    def _developer_name(self, host: Host, game: dict[str, Any]) -> str | None:
        """游戏档案里只有 developerId，机构名要另查一次档案接口。"""
        name = _text(game.get("orgName"))
        if name:
            return name
        developer_id = game.get("developerId")
        if not developer_id:
            return None
        data = self._get_data(host, ARCHIVE_URL, {"orgId": str(developer_id)})
        return _text(_as_mapping(data.get("org")).get("name"))

    def _get_data(self, host: Host, url: str, params: dict[str, Any]) -> dict[str, Any]:
        self._ensure_token(host)
        try:
            return self._request(host, url, params)
        except SourceAuthError:
            # 令牌可能被提前作废，丢掉缓存再取一次；这一次失败就直接抛给宿主。
            self._forget_token()
            self._ensure_token(host)
            return self._request(host, url, params)

    def _request(self, host: Host, url: str, params: dict[str, Any]) -> dict[str, Any]:
        body = host.get_json(url, params=params, headers=self._headers())
        return self._parse(body)

    def _parse(self, body: Any) -> dict[str, Any]:
        if not isinstance(body, dict):
            raise SourceError("月幕返回的不是对象")
        if body.get("success") is False:
            message = _text(body.get("msg")) or "月幕返回失败"
            if body.get("code") in AUTH_CODES:
                raise SourceAuthError(message)
            raise SourceError(message)
        return _as_mapping(body.get("data"))

    def _client_id(self, host: Host) -> str:
        return _text(getattr(host.settings, "client_id", "")) or ""

    def _client_secret(self, host: Host) -> str:
        return _text(getattr(host.settings, "client_secret", "")) or ""

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._access_token}",
            "Accept": "application/json;charset=utf-8",
            "version": "1",
        }

    def _forget_token(self) -> None:
        self._access_token = None
        self._expires_at = 0.0

    def _ensure_token(self, host: Host) -> None:
        if self._access_token and self._expires_at - TOKEN_MARGIN > time.monotonic():
            return
        client_id = self._client_id(host)
        client_secret = self._client_secret(host)
        if not client_id or not client_secret:
            raise SourceAuthError("月幕需要 client_id 和 client_secret")
        body = host.post_json(
            TOKEN_URL,
            data={
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": client_secret,
                "scope": "public",
            },
        )
        token = _text(body.get("access_token")) if isinstance(body, dict) else None
        if token is None:
            error = ""
            if isinstance(body, dict):
                error = _text(body.get("error_description")) or _text(body.get("error")) or ""
            raise SourceAuthError(f"月幕令牌获取失败：{error or '没有 access_token'}")
        expires_in = body.get("expires_in") if isinstance(body, dict) else None
        if not isinstance(expires_in, int | float) or expires_in <= 0:
            expires_in = DEFAULT_EXPIRES_IN
        self._access_token = token
        self._expires_at = time.monotonic() + float(expires_in)


PLUGIN = YmgalSource()
