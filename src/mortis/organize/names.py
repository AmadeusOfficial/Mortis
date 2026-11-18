import re
import unicodedata
from dataclasses import dataclass

_BRACKETS = re.compile(r"\[[^\]]*\]|【[^】]*】|\{[^}]*\}")
# 方括号里的规格词：分辨率、编码、片源、字幕语言。字幕组名一般不带这些。
_SPEC = re.compile(
    r"(?i)(?<![a-z0-9])(?:[0-9]{3,4}[pi]|2160|1080|720|480|4k|uhd|hdr|x26[45]|h\.?26[45]|"
    r"hevc|avc|10bit|8bit|bdrip|brrip|blu-?ray|bd|remux|web-?dl|webrip|hdtv|dvdrip|flac|"
    r"aac|ac3|eac3|dts|truehd|atmos|mkv|mp4|chs|cht|gb|big5|jp|jpn|eng|sc|tc)(?![a-z0-9])"
)
_YEAR = re.compile(r"(?<![0-9])(19[0-9]{2}|20[0-9]{2})(?![0-9])")
# 只收不会出现在片名里的发布标记；web、bd、complete 这类常见词不收。
_JUNK = re.compile(
    r"(?i)(?<![a-z0-9])("
    r"2160p|1080p|1080i|720p|576p|480p|4k|uhd|hdr10\+?|hdr|dovi|"
    r"blu-?ray|bdrip|brrip|bdremux|remux|bd(?:1080|720|2160)p?|web-?dl|webrip|hdtv|"
    r"dvdrip|dvdscr|hdrip|x264|x265|h\.?264|h\.?265|hevc|avc|10bit|8bit|"
    r"aac|ac3|eac3|dts|truehd|atmos|proper|repack|unrated|uncut|extended|director'?s cut|"
    r"remastered|imax|"
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
# 开头方括号一般是字幕组，但 `[哪吒之魔童闹海].Ne.Zha.2.2025` 这种放的是中文片名。
_LEADING_BRACKET = re.compile(r"^\s*(?:\[([^\]]*)\]|【([^】]*)】)")
_GROUP_WORDS = re.compile(r"(?i)字幕|汉化|漢化|压制|壓制|搬运|工作室|[组組社]$|sub|raws")
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


def _brackets(text: str) -> str:
    """去掉字幕组和规格的方括号；整个名字都是方括号时，取第一个像片名的那个。"""
    groups = list(_BRACKETS.finditer(text))
    if not groups:
        return text
    kept = [
        match
        for index, match in enumerate(groups)
        if index > 0 and not _SPEC.search(match.group(0))
    ]
    if kept and not _BRACKETS.sub(" ", text).strip():
        return kept[0].group(0).strip("[]【】{}")
    return _BRACKETS.sub(" ", text)


def _variants(title: str) -> tuple[str, ...]:
    tokens = title.split(" ")
    cjk = " ".join(token for token in tokens if _CJK.search(token))
    latin = " ".join(token for token in tokens if not _CJK.search(token))
    result = [title, _clean(_CHAPTER.sub(" ", title))]
    if cjk and _LATIN.search(latin):
        result += [cjk, latin]
    return tuple(dict.fromkeys(item for item in result if item))


def _leading_title(text: str) -> str | None:
    match = _LEADING_BRACKET.match(text)
    if match is None:
        return None
    inner = (match.group(1) or match.group(2) or "").strip()
    if not _CJK.search(inner) or _SPEC.search(inner) or _GROUP_WORDS.search(inner):
        return None
    return inner


def parse_name(name: str) -> ParsedName:
    text = unicodedata.normalize("NFKC", name)
    leading = _leading_title(text)
    text = _brackets(text)
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
    variants = _variants(title)
    if leading and leading not in variants:
        variants += (leading,)
    return ParsedName(title=title, year=year, search_titles=variants)


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


_GAME_SEPARATORS = " -–—:：/\\_.,·・|"
_GAME_VERSION_TERMS = (
    "初回限定版",
    "汉化硬盘版",
    "简体中文",
    "繁体中文",
    "免安装",
    "豪华版",
    "通常版",
    "完整版",
    "硬盘版",
    "汉化版",
    "PKG版",
    "DL版",
)
# 游戏目录名里的品牌、汉化组、版本标记几乎都在括号里，整组去掉，连在一起的空格一起收掉。
_GAME_BRACKETS = re.compile(r"\s*(?:\[[^\]]*\]|【[^】]*】|\{[^}]*\}|\([^()]*\)|（[^（）]*）)\s*")
_GAME_VERSION_NUMBER = re.compile(
    r"\s*(?<![a-z0-9])(?:ver\.?|v)\s?[0-9]+(?:\.[0-9]+)*(?![a-z0-9])\s*", re.IGNORECASE
)
# 长词在前，免得「汉化硬盘版」只被当成「硬盘版」。
_GAME_VERSION_WORDS = re.compile(
    r"\s*(?<![a-z0-9])(?:"
    + "|".join(sorted(map(re.escape, _GAME_VERSION_TERMS), key=len, reverse=True))
    + r")(?![a-z0-9])\s*",
    re.IGNORECASE,
)


def clean_game_name(name: str) -> str:
    """游戏目录名去掉括号标记、版本号和版本词；全都去光时返回原名。"""
    # 不做 NFKC：标题里的「～」等正文符号要保持原样。
    text = _GAME_BRACKETS.sub(" ", name)
    text = _GAME_VERSION_NUMBER.sub(" ", text)
    text = _GAME_VERSION_WORDS.sub(" ", text)
    text = re.sub(r"\s+", " ", text).lstrip(_GAME_SEPARATORS + " ")
    # 只收掉和标题隔开的分隔符，标题自身的结尾「-」要留着。
    text = re.sub(rf"\s+[{re.escape(_GAME_SEPARATORS)}]+$", "", text).rstrip()
    return text or name.strip()


def parse_folder_name(folder_name: str, file_stem: str) -> ParsedName:
    """一个目录一部电影时用目录名；目录名没有年份而文件名有，就用文件名。"""
    parsed = parse_name(folder_name)
    if parsed.year is None:
        from_file = parse_name(strip_stack(file_stem)[0])
        if from_file.year is not None:
            return from_file
    return parsed
