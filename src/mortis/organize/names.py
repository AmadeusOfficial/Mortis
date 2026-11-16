import re
import unicodedata
from dataclasses import dataclass

_BRACKETS = re.compile(r"\[[^\]]*\]|【[^】]*】|\{[^}]*\}")
_YEAR = re.compile(r"(?<![0-9])(19[0-9]{2}|20[0-9]{2})(?![0-9])")
# 只收不会出现在片名里的发布标记；web、bd、complete 这类常见词不收。
_JUNK = re.compile(
    r"(?i)(?<![a-z0-9])("
    r"2160p|1080p|1080i|720p|576p|480p|4k|uhd|hdr10\+?|hdr|dovi|"
    r"blu-?ray|bdrip|brrip|bdremux|remux|bd(?:1080|720|2160)p?|web-?dl|webrip|hdtv|"
    r"dvdrip|dvdscr|hdrip|x264|x265|h\.?264|h\.?265|hevc|avc|10bit|8bit|"
    r"aac|ac3|eac3|dts|truehd|atmos|proper|repack|unrated|remastered|imax|"
    r"[0-9]{3,4}x[0-9]{3,4}"
    r")(?![a-z0-9])"
    r"|中英字幕|国英双语|中字|双语"
)
_PAREN = re.compile(r"\(([^()]*)\)")
# 文件名常写「第01章」，TMDB 写「第一章」，带着章节号搜不到。
_CHAPTER = re.compile(r"(?:^|\s)-?\s*第\s*[0-9]+\s*[章話话部集](?=\s|$)")
_STACK = re.compile(
    r"(?i)[ ._-]*[\[(]?(?:cd|dvd|part|pt|disc|disk)[ ._-]*([0-9]{1,2}|[a-d])[\])]?$"
)
_CJK = re.compile(r"[\u3040-\u30ff\u3400-\u9fff\uf900-\ufaff\uac00-\ud7af]")
_LATIN = re.compile(r"[A-Za-z]")
_EDGE = " -–—([{_."


@dataclass(frozen=True)
class ParsedName:
    title: str
    year: int | None
    search_titles: tuple[str, ...]


def _clean(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip(_EDGE + ")]}").strip()
    if text.count("(") > text.count(")"):
        text += ")"
    return text


def _variants(title: str) -> tuple[str, ...]:
    tokens = title.split(" ")
    cjk = " ".join(token for token in tokens if _CJK.search(token))
    latin = " ".join(token for token in tokens if not _CJK.search(token))
    result = [title, _clean(_CHAPTER.sub(" ", title))]
    if cjk and _LATIN.search(latin):
        result += [cjk, latin]
    return tuple(dict.fromkeys(item for item in result if item))


def parse_name(name: str) -> ParsedName:
    text = unicodedata.normalize("NFKC", name)
    text = _BRACKETS.sub(" ", text)
    text = _PAREN.sub(lambda m: " " if _JUNK.search(m.group(1)) else m.group(0), text)
    text = re.sub(r"[._]+", " ", text)
    year: int | None = None
    cut = len(text)
    # 开头的四位数是片名的一部分（如《1917》《2012》），只认后面的年份；有多个时取最后一个。
    years = [match for match in _YEAR.finditer(text) if text[: match.start()].strip()]
    if years:
        year = int(years[-1].group(1))
        cut = years[-1].start()
    junk = _JUNK.search(text)
    if junk is not None and text[: junk.start()].strip():
        cut = min(cut, junk.start())
    title = _clean(text[:cut]) or _clean(text)
    return ParsedName(title=title, year=year, search_titles=_variants(title))


def strip_stack(stem: str) -> tuple[str, str | None]:
    match = _STACK.search(stem)
    if match is None or match.start() == 0:
        return stem, None
    return stem[: match.start()], match.group(1).lower()


def stack_key(stem: str) -> str:
    return strip_stack(stem)[0].strip(_EDGE).casefold()


def subtitle_belongs_to(subtitle_name: str, base: str) -> bool:
    """字幕名以视频主体开头，并且紧跟分隔符；`Alien` 不会认走 `Aliens.srt`。"""
    name, prefix = subtitle_name.casefold(), base.casefold()
    return name.startswith(prefix) and name[len(prefix) : len(prefix) + 1] in {".", "-", "_", " "}


def parse_folder_name(folder_name: str, file_stem: str) -> ParsedName:
    """一个目录一部电影时用目录名；目录名没有年份而文件名有，就用文件名。"""
    parsed = parse_name(folder_name)
    if parsed.year is None:
        from_file = parse_name(strip_stack(file_stem)[0])
        if from_file.year is not None:
            return from_file
    return parsed
