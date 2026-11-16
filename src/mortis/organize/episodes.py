import re
import unicodedata
from dataclasses import dataclass

_CN_DIGITS = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}
_CN = "一二两三四五六七八九十"
# 前后是数字或字母说明这段并不是独立的季集标记，例如 `x264`、`h265`、`E02extra`。
_START = r"(?<![0-9a-z])"
_END = r"(?![0-9a-z])"
# S01E02、S01.E02、S01 E02、S01_E02，以及 1x02；季号最多 2 位、集号最多 3 位，
# 且 x 前一串数字不能更长，避免吞掉 1280x720 这类分辨率。
_EPISODE = re.compile(
    rf"(?i){_START}(?:s(?P<season>[0-9]{{1,2}})[ ._-]*e|(?P<xseason>[0-9]{{1,2}})x)"
    rf"(?P<episodes>[0-9]{{1,3}}(?:[ ._-]*-[ ._-]*(?:e[ ._-]*)?[0-9]{{1,3}}|\s*e[0-9]{{1,3}})*)"
)
# 集号到集号；`-` 后面只跟数字即视为范围，否则只是集号本身。
_EPISODE_RANGE = re.compile(
    r"(?a)([0-9]{1,3})\s*-\s*(?:e\s*)?([0-9]{1,3})(?![0-9a-z])", re.IGNORECASE
)
_EPISODE_E = re.compile(r"[eE]\s*([0-9]{1,3})(?![0-9a-z])")
# 只有集号：第2集、第02话、EP02、Ep.02、E02。
_CHINESE_EPISODE = re.compile(r"第\s*([0-9]{1,4})\s*[集话話]")
_EP_PREFIX = re.compile(rf"(?i){_START}(?:(?P<ep>ep)[ ._-]?|e)(?P<number>[0-9]{{1,3}}){_END}")
_FIRST_E = re.compile(
    rf"(?i){_START}e(?P<number>[0-9]{{1,3}})(?P<rest>(?:e[0-9]{{1,3}})*(?![0-9a-z]))"
)
# 单独的 SP01 当特别篇的集号；后面紧跟季集标记的不算。
_SPECIAL = re.compile(rf"(?i){_START}sp[ ._-]?([0-9]{{1,3}}){_END}")
# `SP` 只作为独立词出现：后面要么跟集号，要么是分隔符或结尾。
_SP_ONLY = re.compile(rf"(?i){_START}sp(?=[^a-z]|$)")
_RELEASE = re.compile(
    r"(?i)(?<![0-9a-z])(?:19[0-9]{2}|20[0-9]{2}|"
    r"2160p|1080p|1080i|720p|576p|480p|4k|uhd|"
    r"[0-9]{3,4}x[0-9]{3,4}|"
    r"x264|x265|h\.?264|h\.?265|hevc|avc)(?![0-9a-z])"
)
_CN_NUMBER = re.compile(rf"^([{_CN}]{{1,3}})$")
# 特别篇：SP、OVA、OAD、特典作为独立标记，季号固定为 0。
# `SP01`/`SP`/`OVA` 后面是分隔符或结尾；`OVA02`/`OAD2`/`OVA 02` 带集号。
# 后面的数字要排除年份和分辨率，取到的是 1-3 位、且后面不再跟数字字母。
_SPECIAL_MARK = re.compile(
    rf"(?i){_START}(?:sp|ova|oad|特典)(?:[ ._-]*([0-9]{{1,3}})(?![0-9a-z]))?(?![a-z])"
    r"|(?<![0-9a-z])特典"
)
# 季号加裸集号：`S01.02`、`S2-13`、`S02 13`；中间必须有分隔符，避免和 `S01E02` 抢。
_SEASON_BARE = re.compile(
    rf"(?i){_START}s(?P<season>[0-9]{{1,2}})[ ._-]+(?P<number>[0-9]{{1,3}}){_END}"
)
# 字幕组括号里单独的数字：`[01]`、`[06v2]`、`[12END]`。
_BRACKET_NUMBER = re.compile(
    r"(?i)(?<=[\[(])(?P<number>[0-9]{1,3})(?:v(?P<version>[0-9]+)|end)?(?=[\])])"
)
# ` - 01`：横线两边都是空白。
_DASH_NUMBER = re.compile(rf"(?i)(?<=\s)-\s*([0-9]{{1,3}}){_END}")
# 点号裸集号：`漆黑的子弹.01`、`Steins;Gate.01起源…`、`.01][BDRIP]`。
_DOT_NUMBER = re.compile(r"(?<=\.)([0-9]{1,3})(?![0-9a-z.])")
# 点号裸集号前不能是这些东西，否则 `.264`、`.10bit` 会被误判。
_NOT_EPISODE = re.compile(
    r"(?i)(?:x[ ._-]*$|h[ ._-]*$|x26[45][ ._-]*$|"
    r"flac[ ._-]*$|aac[ ._-]*$|hevc[ ._-]*$|ddp[ ._-]*$|dts[ ._-]*$|[ ._-]bit[ ._-]*$)"
)
_EXTRA_FOLDERS = frozenset(
    {"extras", "extra", "bonus", "menu", "特典", "映像特典", "cd", "scans", "fonts"}
)
# 特别篇目录不是花絮，是第 0 季。
_SPECIAL_FOLDERS = frozenset({"specials", "special", "sp", "sps", "特别篇"})
# 花絮：只在独立出现时算，`Edward`、`OPPO` 不算。
_EXTRA_MARK = re.compile(
    rf"(?i){_START}(?:(?:nc)?(?:op|ed)[0-9]{{0,2}}|pv[0-9]{{0,3}}|cm[0-9]{{0,2}}|spot[0-9]{{0,2}}"
    rf"|menu|preview|trailer|teaser|opening){_END}"
)


@dataclass(frozen=True)
class EpisodeNumber:
    season: int | None
    episodes: tuple[int, ...]


def _cn_number(text: str) -> int | None:
    if "十" not in text:
        if len(text) != 1:
            return None
        return _CN_DIGITS.get(text)
    tens, _, ones = text.partition("十")
    if "十" in ones or (tens and len(tens) != 1) or (ones and len(ones) != 1):
        return None
    head = _CN_DIGITS.get(tens) if tens else 1
    tail = _CN_DIGITS.get(ones) if ones else 0
    if head is None or tail is None:
        return None
    return head * 10 + tail


def season_of_folder(name: str) -> int | None:
    text = unicodedata.normalize("NFKC", name).strip()
    lowered = text.casefold()
    if lowered in _SPECIAL_FOLDERS:
        return 0
    match = re.fullmatch(r"(?a)(?:s|season)[ ._-]*([0-9]{1,2})", lowered)
    if match is not None:
        return int(match.group(1))
    match = re.fullmatch(r"第\s*([0-9]{1,2})\s*季", text)
    if match is not None:
        return int(match.group(1))
    match = _CN_NUMBER.match(text[1:-1]) if len(text) >= 3 and text[0] == "第" else None
    if match is not None and text.endswith("季"):
        return _cn_number(match.group(1))
    return None


def _is_release(text: str, start: int, end: int) -> bool:
    token = text[start:end]
    return _RELEASE.fullmatch(token) is not None


def _expand(first: int, rest: str) -> tuple[int, ...] | None:
    """`E01E02` 是列举；`E01-E02`、`E01-02` 是区间。"""
    if "-" in rest:
        values = [first]
        for head, tail in _EPISODE_RANGE.findall(f"{first}{rest}"):
            low, high = int(head), int(tail)
            if low < first or low >= high or high - first > 99:
                return None
            values.extend(range(low + 1, high + 1))
        if len(values) < 2:
            return None
        return tuple(values)

    values = [first] + [int(value) for value in _EPISODE_E.findall(rest)]
    if any(value < 1 for value in values):
        return None
    return tuple(values)


def _seasonal(text: str) -> tuple[int, tuple[int, ...]] | None:
    for match in _EPISODE.finditer(text):
        if _is_release(text, match.start(), match.end()):
            continue
        first = re.match(r"(?a)[0-9]{1,3}", match.group("episodes"))
        assert first is not None
        episodes = _expand(int(first.group()), match.group("episodes")[first.end() :])
        if episodes is None:
            continue
        season = match.group("season") or match.group("xseason")
        return int(season), episodes
    return None


def _sp_episodes(text: str) -> tuple[int, ...] | None:
    # `SP01` 不是 `S` 加季号，所以要先于季集规则之外的松散集号解析。
    if not _SP_ONLY.search(text):
        return None
    found = [match for match in _SPECIAL.finditer(text) if match.group(0).strip()]
    numbers = [int(match.group(1)) if match.group(1) else 1 for match in found]
    if not numbers or any(number < 1 for number in numbers):
        return None
    return tuple(numbers)


def _special_episodes(text: str) -> tuple[int, ...] | None:
    """`SP`、`OVA`、`OAD`、`特典` 作为独立标记时为特别篇，季号是 0。"""
    numbers: list[int] = []
    for match in _SPECIAL_MARK.finditer(text):
        number = match.group(1)
        if number is None:
            numbers.append(1)
            continue
        if _is_not_episode(text, match.start(1), match.end(1)):
            numbers.append(1)
            continue
        numbers.append(int(number))
    if not numbers or any(number < 1 for number in numbers):
        return None
    return tuple(numbers)


def _is_not_episode(text: str, start: int, end: int) -> bool:
    """点号裸集号前紧挨着编码、音轨、分辨率等标记时不是集号。"""
    if _is_release(text, start, end):
        return True
    return _NOT_EPISODE.search(text[:start]) is not None


def _dot_not_episode(text: str, start: int, end: int) -> bool:
    """`.01` 是小数（版本号 `1.5`）时不是集号。"""
    if text[start - 2 : start - 1].isdigit():
        return True
    head = text[: start - 1]
    return _NOT_EPISODE.search(head) is not None or head.casefold() in {"x", "h"}


def _bracket_number(match: re.Match[str]) -> int | None:
    """`[06v2]` 取 6。"""
    return int(match.group("number"))


def _bare_episodes(text: str) -> tuple[int, ...] | None:
    """只有集号：`[01]`、` - 05`、`.01`。"""
    found = [_bracket_number(match) for match in _BRACKET_NUMBER.finditer(text)]
    numbers = [number for number in found if number is not None]
    if numbers:
        return tuple(numbers) if all(number >= 1 for number in numbers) else None
    for pattern, check in ((_DASH_NUMBER, _is_not_episode), (_DOT_NUMBER, _dot_not_episode)):
        for match in pattern.finditer(text):
            if check(text, match.start(1), match.end(1)):
                continue
            number = int(match.group(1))
            if number >= 1:
                return (number,)
    return None


def _loose_episodes(text: str) -> tuple[int, ...] | None:
    specials = _sp_episodes(text)
    if specials is not None:
        return specials
    chinese = [
        int(match.group(1)) for match in _CHINESE_EPISODE.finditer(text) if int(match.group(1)) >= 1
    ]
    first = _FIRST_E.search(text)
    if first is not None and not _is_release(text, first.start(), first.end()):
        values = [int(first.group("number"))]
        values += [int(value) for value in _EPISODE_E.findall(first.group("rest"))]
        if len(values) > 1:
            return tuple(values) if all(value >= 1 for value in values) else None
    found = [
        int(match.group("number"))
        for match in _EP_PREFIX.finditer(text)
        if not _is_release(text, match.start(), match.end())
    ]
    if found and chinese:
        return None
    if found:
        return tuple(found)
    if chinese:
        return tuple(chinese)
    return _bare_episodes(text)


def _season_bare(text: str) -> tuple[int, tuple[int, ...]] | None:
    for match in _SEASON_BARE.finditer(text):
        if _is_not_episode(text, match.start("number"), match.end("number")):
            continue
        number = int(match.group("number"))
        if number < 1:
            continue
        return int(match.group("season")), (number,)
    return None


def is_extra(stem: str, folder: str | None) -> bool:
    """OP、ED、PV、菜单这类花絮。带 `S01E02` 的是正片，集标题里有 Opening 也不算。"""
    if folder is not None:
        if unicodedata.normalize("NFKC", folder).strip().casefold() in _EXTRA_FOLDERS:
            return True
    text = unicodedata.normalize("NFKC", stem)
    return _seasonal(text) is None and _EXTRA_MARK.search(text) is not None


def parse_episode(stem: str, season_folder: str | None) -> EpisodeNumber | None:
    if is_extra(stem, season_folder):
        return None
    text = unicodedata.normalize("NFKC", stem)
    seasonal = _seasonal(text)
    if seasonal is not None:
        return EpisodeNumber(seasonal[0], seasonal[1])
    bare = _season_bare(text)
    if bare is not None:
        return EpisodeNumber(bare[0], bare[1])
    specials = _special_episodes(text)
    if specials is not None:
        return EpisodeNumber(0, specials)
    folder_season = season_of_folder(season_folder) if season_folder is not None else None
    episodes = _loose_episodes(text)
    if episodes is None:
        return None
    return EpisodeNumber(folder_season, episodes)
