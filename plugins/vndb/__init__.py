"""VNDB 来源插件。搜索、详情都用 Kana API 的 POST 查询，引擎要另查发行版。"""

import re
from typing import Any, ClassVar

from mortis.plugin_api import Hit, Host, SourceError, SourcePlugin

API = "https://api.vndb.org/kana"
CHINESE_LANGS = ("zh-Hans", "zh", "zh-Hant")

SEARCH_FIELDS = (
    "id, title, alttitle, titles.lang, titles.title, titles.official, "
    "aliases, released, description, image.url"
)

VN_FIELDS = (
    "title, alttitle, titles{lang,title,latin,official,main}, description, released, "
    "image.url, image.dims, "
    "screenshots.url, screenshots.sexual, screenshots.thumbnail, screenshots.dims, "
    "developers.name, developers.original, tags.name, tags.rating, tags.spoiler, "
    "length_minutes, rating, votecount, extlinks.url, extlinks.label, "
    "va.staff.id, va.staff.name, va.staff.original, va.character.name, va.character.original, "
    "staff.id, staff.name, staff.original, staff.role"
)

_SPOILER = re.compile(r"\[spoiler[^\]]*\].*?\[/spoiler\]", re.DOTALL | re.IGNORECASE)
_URL_TAG = re.compile(r"\[url[^\]]*\](.*?)\[/url\]", re.DOTALL | re.IGNORECASE)
_OTHER_TAG = re.compile(r"\[/?(?:b|i|u|s|code|quote|spoiler)[^\]]*\]", re.IGNORECASE)
_EMPTY_LINES = re.compile(r"\n{3,}")
_FULL_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")

SCENARIO_ROLES = frozenset({"scenario"})
ARTIST_ROLES = frozenset({"art", "chardesign"})


def clean_bbcode(text: str) -> str:
    """去掉 VNDB 描述里的 BBCode。剧透段落整段丢弃，链接只留文字。"""
    if not text:
        return ""
    result = _SPOILER.sub("", text)
    result = _URL_TAG.sub(r"\1", result)
    result = _OTHER_TAG.sub("", result)
    return _EMPTY_LINES.sub("\n\n", result).strip()


def _chinese_title(titles: list[dict[str, Any]]) -> str | None:
    by_lang = {row.get("lang"): row.get("title") for row in titles if row.get("title")}
    for lang in CHINESE_LANGS:
        if by_lang.get(lang):
            return by_lang[lang]
    return None


def _main_title(titles: list[dict[str, Any]]) -> str | None:
    for row in titles:
        if row.get("main") and row.get("title"):
            return row["title"]
    for row in titles:
        if row.get("title"):
            return row["title"]
    return None


def _images(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    images: list[dict[str, Any]] = []
    for row in rows:
        url = row.get("url")
        if not url:
            continue
        image: dict[str, Any] = {"url": url}
        dims = row.get("dims")
        if isinstance(dims, list) and len(dims) == 2:
            image["width"], image["height"] = dims
        images.append(image)
    return images


def _people(rows: list[dict[str, Any]], roles: frozenset[str]) -> list[dict[str, Any]]:
    people: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        if row.get("role") not in roles:
            continue
        external_id = row.get("id")
        if not external_id or external_id in seen:
            continue
        seen.add(external_id)
        people.append(
            {
                "external_id": external_id,
                "name": row.get("name") or "",
                "original_name": row.get("original"),
                "character": None,
                "photo_url": None,
            }
        )
    return people


def _voice_actors(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """同一个声优配多个角色时合并成一条，角色名用「 / 」连接。"""
    merged: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    characters: dict[str, list[str]] = {}
    for row in rows:
        staff = row.get("staff") or {}
        external_id = staff.get("id")
        if not external_id:
            continue
        if external_id not in merged:
            merged[external_id] = {
                "external_id": external_id,
                "name": staff.get("name") or "",
                "original_name": staff.get("original"),
                "character": None,
                "photo_url": None,
            }
            order.append(external_id)
            characters[external_id] = []
        character = row.get("character") or {}
        name = character.get("original") or character.get("name")
        if name and name not in characters[external_id]:
            characters[external_id].append(name)
    people: list[dict[str, Any]] = []
    for external_id in order:
        person = merged[external_id]
        person["character"] = " / ".join(characters[external_id]) or None
        people.append(person)
    return people


class VndbSource(SourcePlugin):
    id = "vndb"
    name = "VNDB"
    data_type = "galgame"
    provider = "vndb"
    requests_per_second: ClassVar[float] = 2.0

    def search(self, host: Host, title: str) -> list[Hit]:
        payload = host.post_json(
            f"{API}/vn",
            json={"filters": ["search", "=", title], "fields": SEARCH_FIELDS, "results": 10},
        )
        hits: list[Hit] = []
        for row in (payload or {}).get("results") or []:
            titles = row.get("titles") or []
            aliases = list(row.get("aliases") or [])
            aliases.extend(t.get("title") for t in titles if t.get("title"))
            hits.append(
                Hit(
                    external_id=row["id"],
                    title=_chinese_title(titles) or row.get("title") or "",
                    original_title=row.get("alttitle") or _main_title(titles),
                    aliases=list(dict.fromkeys(aliases)),
                    year=_year(row.get("released")),
                )
            )
        return hits

    def fetch(self, host: Host, external_id: str) -> dict[str, Any]:
        payload = host.post_json(
            f"{API}/vn", json={"filters": ["id", "=", external_id], "fields": VN_FIELDS}
        )
        results = (payload or {}).get("results") or []
        if not results:
            raise SourceError(f"VNDB 上没有 {external_id}")
        releases = self._releases(host, external_id)
        return {
            "vn": results[0],
            "engine": releases["engine"],
            "release_extlinks": releases["extlinks"],
        }

    def _releases(self, host: Host, external_id: str) -> dict[str, Any]:
        """vn 对象没有引擎，extlinks 里也只有维基和 Wikidata；这两样都在发行版上。"""
        payload = host.post_json(
            f"{API}/release",
            json={
                "filters": ["vn", "=", ["id", "=", external_id]],
                "fields": "engine, extlinks.url, extlinks.label",
                "results": 100,
            },
        )
        engine: str | None = None
        extlinks: list[dict[str, Any]] = []
        for row in (payload or {}).get("results") or []:
            if engine is None and row.get("engine"):
                engine = row["engine"]
            extlinks.extend(row.get("extlinks") or [])
        return {"engine": engine, "extlinks": extlinks}

    def candidates(self, raw: dict[str, Any]) -> dict[str, Any]:
        vn = raw.get("vn") or {}
        titles = vn.get("titles") or []
        values: dict[str, Any] = {
            "title": _chinese_title(titles) or vn.get("title"),
            "original_title": vn.get("alttitle") or _main_title(titles),
            "overview": clean_bbcode(vn.get("description") or ""),
            "release_date": _full_date(vn.get("released")),
            "poster": _images([vn.get("image") or {}]),
            "fanart": _images([row for row in vn.get("screenshots") or [] if _sexual(row) < 1]),
            "brands": [row["name"] for row in vn.get("developers") or [] if row.get("name")],
            "tags": _top_tags(vn.get("tags") or []),
            "engine": raw.get("engine"),
            "play_time": _play_time(vn.get("length_minutes")),
            "ratings": _ratings(vn),
            "website": _website((vn.get("extlinks") or []) + (raw.get("release_extlinks") or [])),
            "voice_actors": _voice_actors(vn.get("va") or []),
            "scenario": _people(vn.get("staff") or [], SCENARIO_ROLES),
            "artists": _people(vn.get("staff") or [], ARTIST_ROLES),
        }
        return {
            key: value
            for key, value in values.items()
            if value is not None and value != "" and value != []
        }


def _top_tags(rows: list[dict[str, Any]]) -> list[str]:
    """只留不剧透的标签，按评分降序取前 20 个名字。"""
    visible = [row for row in rows if row.get("spoiler") == 0 and row.get("name")]
    ordered = sorted(visible, key=lambda row: row.get("rating") or 0, reverse=True)
    return [row["name"] for row in ordered[:20]]


def _year(released: str | None) -> int | None:
    if not released or len(released) < 4 or not released[:4].isdigit():
        return None
    return int(released[:4])


def _full_date(released: str | None) -> str | None:
    if released and _FULL_DATE.fullmatch(released):
        return released
    return None


def _sexual(screenshot: dict[str, Any]) -> float:
    value = screenshot.get("sexual")
    return float(value) if isinstance(value, int | float) else 0.0


def _play_time(minutes: Any) -> float | None:
    if not isinstance(minutes, int | float) or minutes <= 0:
        return None
    return round(minutes / 60, 1)


def _ratings(vn: dict[str, Any]) -> list[dict[str, Any]]:
    rating = vn.get("rating")
    if not isinstance(rating, int | float):
        return []
    return [
        {
            "source": "vndb",
            "rating": rating,
            "max_rating": 100,
            "vote_count": vn.get("votecount"),
        }
    ]


def _website(extlinks: list[dict[str, Any]]) -> str | None:
    for row in extlinks:
        if row.get("label") == "Official website" and row.get("url"):
            return row["url"]
    return None


PLUGIN = VndbSource()
