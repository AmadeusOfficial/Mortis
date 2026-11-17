"""Bangumi 来源插件，只填 Galgame 字段。网络请求一律走宿主的 get_json、post_json。"""

import re
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field

from mortis.plugin_api import Account, Hit, Host, OAuth, SourceError, SourcePlugin

SEARCH_URL = "https://api.bgm.tv/v0/search/subjects"
ME_URL = "https://api.bgm.tv/v0/me"
SUBJECT_URL = "https://api.bgm.tv/v0/subjects"

# infobox 的键里含这些词的算品牌；含「日期」的键是发售日，不是品牌。
BRAND_KEYWORDS = ("开发", "品牌", "发行")
DATE_KEYWORDS = ("日期", "时间")
WEBSITE_KEYWORDS = ("官方网站", "website")

DATE_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}")
TAG_LIMIT = 20


class BangumiSecrets(BaseModel):
    model_config = ConfigDict(extra="ignore")

    app_id: str = Field("", title="App ID")
    app_secret: str = Field("", title="App Secret")
    access_token: str = Field(
        "",
        title="访问令牌",
        description="登录后自动填写；也可以直接填 next.bgm.tv/demo/access-token 生成的个人令牌",
    )
    refresh_token: str = ""
    expires_at: int = 0


def _text(value: Any) -> str | None:
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return None


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _entry_values(entry: dict[str, Any]) -> list[str]:
    """infobox 的值可能是字符串，也可能是 [{"k": ..., "v": ...}] 列表。"""
    raw = entry.get("value")
    if isinstance(raw, list):
        texts = [_text(item.get("v")) if isinstance(item, dict) else _text(item) for item in raw]
    else:
        texts = [_text(raw)]
    return [text for text in texts if text]


def _infobox_values(subject: dict[str, Any], keywords: tuple[str, ...]) -> list[str]:
    values: list[str] = []
    for entry in _as_list(subject.get("infobox")):
        if not isinstance(entry, dict):
            continue
        key = (_text(entry.get("key")) or "").casefold()
        if any(word.casefold() in key for word in keywords):
            values.extend(_entry_values(entry))
    return values


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _brands(subject: dict[str, Any]) -> list[str]:
    """含「发行」的键也可能是「发行日期」，带日期字样的键跳过。"""
    values: list[str] = []
    for entry in _as_list(subject.get("infobox")):
        if not isinstance(entry, dict):
            continue
        key = _text(entry.get("key")) or ""
        if any(word in key for word in DATE_KEYWORDS):
            continue
        if any(word in key for word in BRAND_KEYWORDS):
            values.extend(_entry_values(entry))
    return _unique(values)


def _tag_names(subject: dict[str, Any]) -> list[str]:
    tags = [tag for tag in _as_list(subject.get("tags")) if isinstance(tag, dict)]
    tags.sort(key=lambda tag: tag.get("count") or 0, reverse=True)
    names = [_text(tag.get("name")) for tag in tags[:TAG_LIMIT]]
    return _unique([name for name in names if name])


def _ratings(subject: dict[str, Any]) -> list[dict[str, Any]]:
    rating = subject.get("rating")
    if not isinstance(rating, dict):
        return []
    score = rating.get("score")
    if not isinstance(score, int | float) or score <= 0:
        return []
    return [
        {
            "source": "bangumi",
            "rating": score,
            "max_rating": 10,
            "vote_count": rating.get("total"),
        }
    ]


def _photo_url(person: dict[str, Any]) -> str | None:
    images = person.get("images")
    if not isinstance(images, dict):
        return None
    return _text(images.get("medium"))


def _voice_actors(characters: list[Any]) -> list[dict[str, Any]]:
    """同一声优配多个角色时合并成「A / B」，按首次出现的顺序。"""
    merged: dict[str, dict[str, Any]] = {}
    roles: dict[str, list[str]] = {}
    for character in characters:
        if not isinstance(character, dict):
            continue
        character_name = _text(character.get("name"))
        for actor in _as_list(character.get("actors")):
            if not isinstance(actor, dict) or actor.get("id") is None:
                continue
            external_id = str(actor.get("id"))
            if external_id not in merged:
                merged[external_id] = {
                    "external_id": external_id,
                    "name": _text(actor.get("name")) or "",
                    "original_name": None,
                    "character": None,
                    "photo_url": _photo_url(actor),
                }
                roles[external_id] = []
            if character_name and character_name not in roles[external_id]:
                roles[external_id].append(character_name)

    result: list[dict[str, Any]] = []
    for external_id, person in merged.items():
        person["character"] = " / ".join(roles[external_id]) or None
        result.append(person)
    return result


def _crew(persons: list[Any], relations: tuple[str, ...]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for person in persons:
        if not isinstance(person, dict) or person.get("id") is None:
            continue
        if person.get("relation") not in relations:
            continue
        result.append(
            {
                "external_id": str(person.get("id")),
                "name": _text(person.get("name")) or "",
                "original_name": None,
                "character": None,
                "photo_url": _photo_url(person),
            }
        )
    return result


def _drop_empty(candidates: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in candidates.items() if value not in (None, "", [])}


class BangumiSource(SourcePlugin):
    id: ClassVar[str] = "bangumi"
    name: ClassVar[str] = "Bangumi"
    data_type: ClassVar[str] = "galgame"
    provider: ClassVar[str] = "bangumi"
    Secrets = BangumiSecrets
    oauth: ClassVar[OAuth | None] = OAuth(
        authorize_url="https://bgm.tv/oauth/authorize",
        token_url="https://bgm.tv/oauth/access_token",
        register_url="https://bgm.tv/dev/app",
        token_page_url="https://next.bgm.tv/demo/access-token",
    )
    requests_per_second: ClassVar[float] = 2

    def _headers(self, host: Host) -> dict[str, str]:
        # 令牌由宿主续期，插件只负责带上。
        token = host.access_token()
        return {"Authorization": f"Bearer {token}"} if token else {}

    def disabled_reason(self, host: Host) -> str | None:
        if host.access_token():
            return None
        return "没登录 Bangumi 时搜不到 18 禁条目"

    def account(self, host: Host) -> Account | None:
        headers = self._headers(host)
        if not headers:
            return None
        me = host.get_json(ME_URL, headers=headers)
        if not isinstance(me, dict):
            raise SourceError("Bangumi 账号信息返回的不是对象")
        username = _text(me.get("username")) or str(me.get("id") or "")
        avatar = me.get("avatar")
        return Account(
            name=_text(me.get("nickname")) or username,
            avatar_url=_text(avatar.get("medium")) if isinstance(avatar, dict) else None,
            url=f"https://bgm.tv/user/{username}" if username else None,
        )

    def search(self, host: Host, title: str) -> list[Hit]:
        body = host.post_json(
            f"{SEARCH_URL}?limit=10",
            json={"keyword": title, "filter": {"type": [4], "nsfw": True}},
            headers=self._headers(host),
        )
        if not isinstance(body, dict):
            raise SourceError("Bangumi 搜索返回的不是对象")

        hits: list[Hit] = []
        for item in _as_list(body.get("data")):
            if not isinstance(item, dict) or item.get("id") is None:
                continue
            name = _text(item.get("name"))
            display = _text(item.get("name_cn")) or name
            if display is None:
                continue
            images = item.get("images")
            hits.append(
                Hit(
                    external_id=str(item.get("id")),
                    title=display,
                    original_title=name,
                    year=_year(item.get("date")),
                    overview=_text(item.get("summary")),
                    poster_url=_text(images.get("large")) if isinstance(images, dict) else None,
                )
            )
        host.log(f"Bangumi 搜索「{title}」拿到 {len(hits)} 条")
        return hits

    def fetch(self, host: Host, external_id: str) -> dict[str, Any]:
        headers = self._headers(host)
        subject = host.get_json(f"{SUBJECT_URL}/{external_id}", headers=headers)
        if not isinstance(subject, dict):
            raise SourceError(f"Bangumi 条目 {external_id} 返回的不是对象")
        characters = host.get_json(f"{SUBJECT_URL}/{external_id}/characters", headers=headers)
        persons = host.get_json(f"{SUBJECT_URL}/{external_id}/persons", headers=headers)
        return {
            "subject": subject,
            "characters": _as_list(characters),
            "persons": _as_list(persons),
        }

    def candidates(self, raw: dict[str, Any]) -> dict[str, Any]:
        subject = raw.get("subject")
        if not isinstance(subject, dict):
            return {}
        images = subject.get("images")
        cover = _text(images.get("large")) if isinstance(images, dict) else None
        website = _infobox_values(subject, WEBSITE_KEYWORDS)
        candidates: dict[str, Any] = {
            "title": _text(subject.get("name_cn")) or _text(subject.get("name")),
            "original_title": _text(subject.get("name")),
            "overview": _text(subject.get("summary")),
            "release_date": _release_date(subject.get("date")),
            "poster": [{"url": cover, "language": None, "width": None, "height": None}]
            if cover
            else [],
            "brands": _brands(subject),
            "tags": _tag_names(subject),
            "ratings": _ratings(subject),
            "website": website[0] if website else None,
            "voice_actors": _voice_actors(_as_list(raw.get("characters"))),
            "scenario": _crew(_as_list(raw.get("persons")), ("剧本", "脚本")),
            "artists": _crew(_as_list(raw.get("persons")), ("原画",)),
        }
        return _drop_empty(candidates)


def _year(value: Any) -> int | None:
    text = _text(value) or ""
    if len(text) >= 4 and text[:4].isdigit():
        return int(text[:4])
    return None


def _release_date(value: Any) -> str | None:
    text = _text(value) or ""
    return text if DATE_PATTERN.fullmatch(text) else None


PLUGIN = BangumiSource()
