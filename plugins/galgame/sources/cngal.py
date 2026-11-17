"""CnGal（中文 Galgame 资料站）来源插件。"""

import html
import re
from datetime import datetime, timedelta, timezone
from typing import Any, ClassVar

from mortis.plugin_api import Hit, Host, NoSecrets, NoSettings, SourceError, SourcePlugin

SEARCH_URL = "https://api.cngal.org/api/home/Search"
ENTRY_URL = "https://api.cngal.org/api/entries/GetEntryView"

CHINA_TIME = timezone(timedelta(hours=8))

# CnGal 的正文是 HTML，日文原名靠假名判断。
_JAPANESE = re.compile(r"[\u3040-\u30ff]")
_TAG = re.compile(r"<[^>]+>")
_MARKDOWN_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_MARKDOWN_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_ISO_DATE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
_SPLIT = re.compile(r"[，,、;；/]")

SCENARIO_WORDS = ("剧本", "编剧")
ARTIST_WORDS = ("原画", "美术")


def _plain(value: str) -> str:
    """把正文里的图片和链接去掉，留下纯文本。"""
    text = _MARKDOWN_IMAGE.sub("", value)
    text = _MARKDOWN_LINK.sub(r"\1", text)
    text = _TAG.sub("\n", text)
    lines = [line.strip() for line in html.unescape(text).splitlines()]
    return "\n".join(line for line in lines if line)


def _text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = html.unescape(value).strip()
    return stripped or None


def _date(value: Any) -> str | None:
    """CnGal 存的是中国本地时间的零点，换算成 UTC+8 再取日期，否则会差一天。"""
    if not isinstance(value, str):
        return None
    found = _ISO_DATE.search(value)
    if found is None:
        return None
    try:
        moment = datetime.fromisoformat(value.strip())
    except ValueError:
        return f"{found.group(1)}-{found.group(2)}-{found.group(3)}"
    if moment.tzinfo is not None:
        moment = moment.astimezone(CHINA_TIME)
    return moment.date().isoformat()


def _year(value: Any) -> int | None:
    found = _date(value)
    return int(found[:4]) if found else None


def _info(raw: dict[str, Any], name: str) -> list[str]:
    """信息和「附加信息」里同名字段的值，可能有多条。"""
    values: list[str] = []
    for item in raw.get("information") or []:
        if isinstance(item, dict) and item.get("name") == name:
            values.append(str(item.get("value") or ""))
    for item in raw.get("allInformation") or []:
        if isinstance(item, dict) and item.get("displayName") == name:
            values.append(str(item.get("displayValue") or ""))
    result = []
    for value in values:
        cleaned = _TAG.sub("", value).strip()
        if cleaned:
            result.append(cleaned)
    return result


def _people(external_key: str, name: str, character: str | None) -> dict[str, Any]:
    return {
        "external_id": external_key,
        "name": name,
        "original_name": None,
        "character": character,
        "photo_url": None,
    }


def _person_key(entry_id: Any, name: str) -> str:
    return str(entry_id) if entry_id else name


def _dedupe(people: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result = []
    for person in people:
        if person["external_id"] in seen:
            continue
        seen.add(person["external_id"])
        result.append(person)
    return result


def _voice_actors(raw: dict[str, Any]) -> list[dict[str, Any]]:
    people = []
    for role in raw.get("roles") or []:
        if not isinstance(role, dict):
            continue
        character = _text(role.get("name"))
        cvs: list[tuple[str, Any]] = []
        for info in role.get("addInfors") or []:
            if isinstance(info, dict) and info.get("modifier") == "配音":
                for content in info.get("contents") or []:
                    if not isinstance(content, dict):
                        continue
                    name = _text(content.get("displayName"))
                    if name:
                        cvs.append((name, content.get("id")))
        if not cvs:
            fallback = _text(role.get("cv"))
            if fallback:
                cvs.append((fallback, None))
        for name, entry_id in cvs:
            people.append(_people(_person_key(entry_id, name), name, character))
    return _dedupe(people)


def _staffs(raw: dict[str, Any], words: tuple[str, ...]) -> list[dict[str, Any]]:
    people = []
    for group in raw.get("staffs") or []:
        if not isinstance(group, dict):
            continue
        for item in group.get("staffList") or []:
            if not isinstance(item, dict):
                continue
            position = f"{item.get('modifier') or ''} {item.get('positionGeneral') or ''}"
            if not any(word in position for word in words):
                continue
            for name_item in item.get("names") or []:
                if not isinstance(name_item, dict):
                    continue
                name = _text(name_item.get("displayName"))
                if name is None:
                    continue
                people.append(_people(_person_key(name_item.get("id"), name), name, None))
    return _dedupe(people)


def _release_date(raw: dict[str, Any]) -> str | None:
    stated = _info(raw, "发行时间")
    if stated:
        return _date(stated[0])
    releases = [r for r in raw.get("releases") or [] if isinstance(r, dict)]
    official = [r for r in releases if r.get("type") == "Official"]
    for release in [*official, *releases]:
        found = _date(release.get("time"))
        if found:
            return found
    return None


def _original_title(raw: dict[str, Any]) -> str | None:
    another = _text(raw.get("anotherName"))
    if another is None:
        return None
    parts = [part.strip() for part in _SPLIT.split(another) if part.strip()]
    for part in parts:
        if _JAPANESE.search(part):
            return part
    return another if _JAPANESE.search(another) else None


def _overview(raw: dict[str, Any]) -> str | None:
    main_page = raw.get("mainPage")
    if isinstance(main_page, str):
        text = _plain(main_page)
        if text:
            return text
    overview = _text(raw.get("briefIntroduction"))
    return _plain(overview) if overview else None


class CngalSource(SourcePlugin):
    id: ClassVar[str] = "cngal"
    name: ClassVar[str] = "CnGal"
    data_type: ClassVar[str] = "galgame"
    provider: ClassVar[str] = "cngal"
    Settings: ClassVar[type[NoSettings]] = NoSettings
    Secrets: ClassVar[type[NoSecrets]] = NoSecrets
    requests_per_second: ClassVar[float] = 1.0

    def search(self, host: Host, title: str) -> list[Hit]:
        payload = host.get_json(SEARCH_URL, params={"Types": "Game", "Text": title})
        if not isinstance(payload, dict):
            return []
        paged = payload.get("pagedResultDto")
        rows = paged.get("data") if isinstance(paged, dict) else None
        hits = []
        for row in rows or []:
            entry = row.get("entry") if isinstance(row, dict) else None
            if not isinstance(entry, dict) or entry.get("id") is None:
                continue
            if entry.get("type") not in (None, "Game"):
                continue
            name = _text(entry.get("displayName")) or _text(entry.get("name"))
            if name is None:
                continue
            hits.append(
                Hit(
                    external_id=str(entry["id"]),
                    title=name,
                    year=_year(entry.get("publishTime")),
                    overview=_text(entry.get("briefIntroduction")),
                    poster_url=_text(entry.get("mainImage")),
                )
            )
        return hits

    def fetch(self, host: Host, external_id: str) -> dict[str, Any]:
        payload = host.get_json(f"{ENTRY_URL}/{external_id}")
        if not isinstance(payload, dict):
            raise SourceError("CnGal 返回的详情不是对象")
        return payload

    def candidates(self, raw: dict[str, Any]) -> dict[str, Any]:
        values: dict[str, Any] = {}
        title = _text(raw.get("displayName")) or _text(raw.get("name"))
        if title:
            values["title"] = title
        original_title = _original_title(raw)
        if original_title:
            values["original_title"] = original_title
        overview = _overview(raw)
        if overview:
            values["overview"] = overview
        release_date = _release_date(raw)
        if release_date:
            values["release_date"] = release_date

        brands = [
            _text(group.get("displayName"))
            for group in raw.get("productionGroups") or []
            if isinstance(group, dict)
        ]
        brands = [brand for brand in brands if brand]
        if not brands:
            for value in _info(raw, "制作组"):
                brands.extend(part.strip() for part in _SPLIT.split(value) if part.strip())
        if brands:
            values["brands"] = list(dict.fromkeys(brands))

        tags = []
        for tag in raw.get("tags") or []:
            name = _text(tag.get("name")) if isinstance(tag, dict) else None
            if name:
                tags.append(name)
        if tags:
            values["tags"] = list(dict.fromkeys(tags))

        poster = _text(raw.get("mainPicture")) or _text(raw.get("mainImage"))
        if poster:
            values["poster"] = [{"url": poster, "language": None, "width": None, "height": None}]
        voice_actors = _voice_actors(raw)
        if voice_actors:
            values["voice_actors"] = voice_actors
        scenario = _staffs(raw, SCENARIO_WORDS)
        if scenario:
            values["scenario"] = scenario
        artists = _staffs(raw, ARTIST_WORDS)
        if artists:
            values["artists"] = artists
        website = _info(raw, "官网")
        if website and website[0].startswith("http"):
            values["website"] = website[0]
        return values


PLUGIN = CngalSource()
