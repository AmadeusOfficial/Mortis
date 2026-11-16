"""把 TMM 写法的重命名模板渲染成一个路径片段（目录名或去扩展名的文件名）。"""

import re
from collections.abc import Mapping

TOKENS = (
    "title",
    "originalTitle",
    "year",
    "edition",
    "videoFormat",
    "audioCodec",
    "tmdb",
    "imdb",
)

_IDENT = re.compile(r"[A-Za-z][A-Za-z0-9]*\Z")
_FORBIDDEN = '<>"\\/|?*'
_SEPARATORS = ".-_"
_DEVICE_NAMES = (
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{index}" for index in range(1, 10)}
    | {f"LPT{index}" for index in range(1, 10)}
)


def _bad_template(original: str) -> ValueError:
    return ValueError(f"模板写法不对：{original}")


def _clean_value(value: str | None, colon: str) -> str:
    """去掉 Windows 不允许的字符，并把冒号换成 colon。"""
    if value is None:
        return ""
    out: list[str] = []
    for char in value:
        if char in ":：":
            while out and out[-1] == " ":
                out.pop()
            if colon:
                out.append(colon)
            continue
        if char in _FORBIDDEN or ord(char) < 32:
            continue
        out.append(char)
    text = "".join(out)
    return text if text.strip() else ""


def _expand(body: str, original: str, tokens: Mapping[str, str | None], colon: str) -> str:
    fields = body.split(",")
    if len(fields) == 1:
        if not _IDENT.match(fields[0]):
            raise _bad_template(original)
        return _clean_value(tokens.get(fields[0]), colon)
    if len(fields) == 3:
        if not _IDENT.match(fields[1]):
            raise _bad_template(original)
        value = _clean_value(tokens.get(fields[1]), colon)
        return f"{fields[0]}{value}{fields[2]}" if value else ""
    raise _bad_template(original)


def _collapse(text: str, space: str) -> str:
    """把重复的分隔符收成一个，并去掉挨着 space 的句点。"""
    kept: list[str] = []
    for char in text:
        if kept and char == kept[-1] and char in _SEPARATORS:
            continue
        kept.append(char)
    text = "".join(kept)
    if not space:
        return text
    # 照 TMM：空格换掉后，夹着破折号的 "War.-.2005"、"War-.Cut" 收成一个替身。
    mark = re.escape(space)
    text = re.sub(rf"(?:{mark})+-+(?:{mark})*|-+(?:{mark})+", space, text)
    if space == ".":
        return text
    for char in sorted(set(space)):
        if char == ".":
            continue
        while True:
            merged = text.replace(f"{char}.", char).replace(f".{char}", char)
            if merged == text:
                break
            text = merged
    return text


def _closing(template: str, index: int) -> int:
    """找到和 "${" 配对的 "}"；前后缀里可以有成对的花括号，如 Plex 的 ${{tmdb-,tmdb,}}。"""
    depth = 0
    for position in range(index, len(template)):
        char = template[position]
        if char == "{":
            depth += 1
        elif char == "}":
            if depth == 0:
                return position
            depth -= 1
    return -1


def render(template: str, tokens: Mapping[str, str | None], *, space: str, colon: str) -> str:
    """按模板生成一个名字。space 是空格替身（空串表示保留空格），colon 是冒号替身。"""
    pieces: list[str] = []
    index = 0
    while True:
        start = template.find("${", index)
        if start < 0:
            pieces.append(template[index:])
            break
        pieces.append(template[index:start])
        end = _closing(template, start + 2)
        if end < 0:
            raise _bad_template(template[start:])
        original = template[start : end + 1]
        pieces.append(_expand(template[start + 2 : end], original, tokens, colon))
        index = end + 1
    text = " ".join("".join(pieces).split())
    if space:
        text = text.replace(" ", space)
    text = _collapse(text, space)
    text = text.strip(" .-_" + space)
    if not text:
        raise ValueError("模板生成的名字为空")
    if text.upper() in _DEVICE_NAMES:
        text += "_"
    return text
