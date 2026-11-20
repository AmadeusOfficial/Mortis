import os
import re
from collections.abc import Iterable
from dataclasses import dataclass, field

from mortis.organize.names import (
    ParsedName,
    clean_game_name,
    parse_folder_name,
    parse_name,
    stack_key,
    strip_stack,
    subtitle_belongs_to,
)

SKIP_DIR_NAMES = frozenset(
    {
        "$recycle.bin",
        "system volume information",
        "@eadir",
        "#recycle",
        ".actors",
        "extrafanart",
        "extrathumbs",
        "extras",
        "featurettes",
        "behind the scenes",
        "deleted scenes",
        "trailers",
        "sample",
        "samples",
        "lost+found",
    }
)
SUBTITLE_DIR_NAMES = frozenset({"subs", "subtitles", "sub", "字幕"})
DISC_DIR_NAMES = frozenset({"video_ts", "bdmv", "hvdvd_ts"})
DISC_MAIN_FILES = ("video_ts.ifo", "index.bdmv")
# 和 PotatoVN 的默认识别规则一样：文件夹里要有 exe，还要有其中一种资源。
GAME_RESOURCE_EXTENSIONS = frozenset(
    {".xp3", ".arc", ".dat", ".ini", ".dll", ".txt", ".pac", ".noa", ".sh", ".bin", ".pck"}
)
MAX_GAME_SCAN_DEPTH = 8
_SAMPLE = re.compile(r"(?i)(^|[-_ .\[(])sample([-_ .\])]|$)")
_TRAILER = re.compile(r"(?i)(^|[-_ .\[(])trailer([-_ .\])]|$)")


@dataclass
class FoundMovie:
    folder: str
    name: str
    parsed: ParsedName
    videos: list[str]
    subtitles: list[str]


@dataclass
class ScanResult:
    movies: list[FoundMovie] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass
class _Walk:
    videos: frozenset[str]
    subtitles: frozenset[str]
    skip: frozenset[str]
    result: ScanResult


def _split(name: str) -> tuple[str, str]:
    stem, ext = os.path.splitext(name)
    return stem, ext.lower()


def _list(folder: str, walk: _Walk) -> tuple[list[os.DirEntry[str]], list[os.DirEntry[str]]] | None:
    try:
        with os.scandir(folder) as handle:
            entries = sorted(handle, key=lambda entry: entry.name.casefold())
    except OSError as error:
        walk.result.errors.append(f"读不了目录 {folder}：{error.strerror or type(error).__name__}")
        return None
    dirs: list[os.DirEntry[str]] = []
    files: list[os.DirEntry[str]] = []
    for entry in entries:
        try:
            if entry.is_dir():
                dirs.append(entry)
            elif entry.is_file():
                files.append(entry)
        except OSError:
            continue
    return dirs, files


def _disc_video(disc: str, walk: _Walk) -> str | None:
    listed = _list(disc, walk)
    if listed is None:
        return None
    _, files = listed
    for entry in files:
        if entry.name.casefold() in DISC_MAIN_FILES:
            return entry.path
    for entry in files:
        if _split(entry.name)[1] in walk.videos:
            return entry.path
    return None


def _part_order(path: str) -> tuple[int, str]:
    part = strip_stack(_split(os.path.basename(path))[0])[1]
    if part is None:
        return (0, "")
    return (int(part), "") if part.isdigit() else (ord(part) - ord("a") + 1, part)


def _walk(folder: str, is_root: bool, walk: _Walk) -> None:
    listed = _list(folder, walk)
    if listed is None:
        return
    dirs, files = listed
    name = os.path.basename(folder)

    if not is_root:
        discs = [entry for entry in dirs if entry.name.casefold() in DISC_DIR_NAMES]
        if discs:
            video = _disc_video(discs[0].path, walk)
            if video is not None:
                walk.result.movies.append(FoundMovie(folder, name, parse_name(name), [video], []))
            return

    groups: dict[str, list[str]] = {}
    bases: dict[str, str] = {}
    subtitles: list[str] = []
    for entry in files:
        stem, ext = _split(entry.name)
        if ext in walk.videos and not _SAMPLE.search(stem) and not _TRAILER.search(stem):
            key = stack_key(stem)
            groups.setdefault(key, []).append(entry.path)
            bases.setdefault(key, strip_stack(stem)[0].strip(" ._-") or stem)
        elif ext in walk.subtitles:
            subtitles.append(entry.path)
    for entry in dirs:
        if entry.name.casefold() in SUBTITLE_DIR_NAMES:
            listed_subs = _list(entry.path, walk)
            if listed_subs is not None:
                subtitles += [
                    sub.path for sub in listed_subs[1] if _split(sub.name)[1] in walk.subtitles
                ]

    if groups and not is_root and len(groups) == 1:
        ((key, videos),) = groups.items()
        walk.result.movies.append(
            FoundMovie(
                folder,
                name,
                parse_folder_name(name, bases[key]),
                sorted(videos, key=_part_order),
                subtitles,
            )
        )
    else:
        for key, videos in groups.items():
            base = bases[key]
            own = [sub for sub in subtitles if subtitle_belongs_to(os.path.basename(sub), base)]
            walk.result.movies.append(
                FoundMovie(folder, base, parse_name(base), sorted(videos, key=_part_order), own)
            )

    for entry in dirs:
        lowered = entry.name.casefold()
        if (
            lowered in walk.skip
            or lowered in SUBTITLE_DIR_NAMES
            or entry.name.startswith((".", "$"))
        ):
            continue
        _walk(entry.path, False, walk)


def _new_walk(
    video_extensions: Iterable[str], subtitle_extensions: Iterable[str], skip_folders: Iterable[str]
) -> _Walk:
    return _Walk(
        videos=frozenset(ext.lower() for ext in video_extensions),
        subtitles=frozenset(ext.lower() for ext in subtitle_extensions),
        skip=SKIP_DIR_NAMES | {name.casefold() for name in skip_folders},
        result=ScanResult(),
    )


def scan_paths(
    roots: Iterable[str],
    *,
    video_extensions: Iterable[str],
    subtitle_extensions: Iterable[str],
    skip_folders: Iterable[str],
) -> ScanResult:
    walk = _new_walk(video_extensions, subtitle_extensions, skip_folders)
    for root in roots:
        if not os.path.isdir(root):
            walk.result.errors.append(f"目录不存在或无法访问：{root}")
            continue
        _walk(root, True, walk)
    return walk.result


def _collect(folder: str, walk: _Walk, videos: list[str], subtitles: list[str]) -> None:
    listed = _list(folder, walk)
    if listed is None:
        return
    dirs, files = listed
    for entry in files:
        stem, ext = _split(entry.name)
        if ext in walk.videos and not _SAMPLE.search(stem) and not _TRAILER.search(stem):
            videos.append(entry.path)
        elif ext in walk.subtitles:
            subtitles.append(entry.path)
    for entry in dirs:
        lowered = entry.name.casefold()
        if lowered in walk.skip or entry.name.startswith((".", "$")):
            continue
        _collect(entry.path, walk, videos, subtitles)


def scan_shows(
    roots: Iterable[str],
    *,
    video_extensions: Iterable[str],
    subtitle_extensions: Iterable[str],
    skip_folders: Iterable[str],
) -> ScanResult:
    """库目录下每个子目录是一部剧，剧目录里的视频不论多深都归这部剧。"""
    walk = _new_walk(video_extensions, subtitle_extensions, skip_folders)
    for root in roots:
        listed = _list(root, walk) if os.path.isdir(root) else None
        if listed is None:
            if not os.path.isdir(root):
                walk.result.errors.append(f"目录不存在或无法访问：{root}")
            continue
        dirs, files = listed
        loose = [entry.name for entry in files if _split(entry.name)[1] in walk.videos]
        if loose:
            walk.result.errors.append(f"库目录 {root} 下有 {len(loose)} 个视频没放在剧目录里，跳过")
        for entry in dirs:
            lowered = entry.name.casefold()
            if lowered in walk.skip or entry.name.startswith((".", "$")):
                continue
            videos: list[str] = []
            subtitles: list[str] = []
            _collect(entry.path, walk, videos, subtitles)
            if videos:
                walk.result.movies.append(
                    FoundMovie(entry.path, entry.name, parse_name(entry.name), videos, subtitles)
                )
    return walk.result


def directory_is_game(folder: str) -> bool:
    """这一层同时有 exe 和一种游戏资源。子目录里的文件不算。"""
    try:
        with os.scandir(folder) as handle:
            names: list[str] = []
            for entry in handle:
                try:
                    if entry.is_file():
                        names.append(entry.name)
                except OSError:
                    continue
    except OSError:
        return False
    has_exe = False
    has_resource = False
    for name in names:
        ext = _split(name)[1]
        if ext == ".exe":
            has_exe = True
        elif ext in GAME_RESOURCE_EXTENSIONS:
            has_resource = True
        if has_exe and has_resource:
            return True
    return False


def _append_game(walk: _Walk, path: str, name: str) -> None:
    title = clean_game_name(name)
    parsed = ParsedName(title=title, year=None, search_titles=(title,))
    walk.result.movies.append(FoundMovie(path, name, parsed, [path], []))


def _descend_games(folder: str, walk: _Walk, depth: int) -> None:
    if depth > MAX_GAME_SCAN_DEPTH:
        walk.result.errors.append(f"目录层级超过 {MAX_GAME_SCAN_DEPTH}，不再往下看：{folder}")
        return
    listed = _list(folder, walk)
    if listed is None:
        return
    dirs, _files = listed
    for entry in dirs:
        lowered = entry.name.casefold()
        if lowered in walk.skip or entry.name.startswith((".", "$")):
            continue
        try:
            is_dir = entry.is_dir(follow_symlinks=False)
        except OSError:
            continue
        if not is_dir:
            continue
        if directory_is_game(entry.path):
            _append_game(walk, entry.path, entry.name)
            continue
        _descend_games(entry.path, walk, depth + 1)


def scan_folders(
    roots: Iterable[str], *, skip_folders: Iterable[str], recursive: bool = False
) -> ScanResult:
    """库目录下每个子目录是一款游戏。

    recursive 为假时不往下钻，一级文件夹都算游戏。
    为真时继续往下找：同时有 exe 和资源文件的文件夹才算，认到就不再进它的子目录。
    """
    # 游戏目录不按扩展名找文件，只借用 _new_walk 的跳过目录集合。
    walk = _new_walk((), (), skip_folders)
    for root in roots:
        if not os.path.isdir(root):
            walk.result.errors.append(f"目录不存在或无法访问：{root}")
            continue
        if recursive:
            _descend_games(root, walk, 1)
            continue
        listed = _list(root, walk)
        if listed is None:
            continue
        dirs, _files = listed
        for entry in dirs:
            lowered = entry.name.casefold()
            if lowered in walk.skip or entry.name.startswith((".", "$")):
                continue
            _append_game(walk, entry.path, entry.name)
    return walk.result
