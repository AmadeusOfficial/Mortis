from pathlib import Path

from mortis.config.models import DEFAULT_SUBTITLE_EXTENSIONS, DEFAULT_VIDEO_EXTENSIONS
from mortis.organize.scan import ScanResult, scan_paths


def _touch(root: Path, *relative: str) -> None:
    for rel in relative:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")


def _scan(root: Path, skip: list[str] | None = None) -> ScanResult:
    return scan_paths(
        [str(root)],
        video_extensions=DEFAULT_VIDEO_EXTENSIONS,
        subtitle_extensions=DEFAULT_SUBTITLE_EXTENSIONS,
        skip_folders=skip if skip is not None else ["MAKEMKV"],
    )


def _by_name(result: ScanResult) -> dict[str, list[str]]:
    return {movie.name: [Path(v).name for v in movie.videos] for movie in result.movies}


def test_one_folder_one_movie_with_parts_and_subs(tmp_path: Path) -> None:
    _touch(
        tmp_path,
        "The Matrix (1999)/The.Matrix.cd2.mkv",
        "The Matrix (1999)/The.Matrix.cd1.mkv",
        "The Matrix (1999)/The.Matrix.zh.srt",
        "The Matrix (1999)/Subs/en.ass",
        "The Matrix (1999)/The.Matrix-sample.mkv",
        "The Matrix (1999)/The.Matrix-trailer.mp4",
        "The Matrix (1999)/poster.jpg",
    )
    result = _scan(tmp_path)
    assert _by_name(result) == {"The Matrix (1999)": ["The.Matrix.cd1.mkv", "The.Matrix.cd2.mkv"]}
    movie = result.movies[0]
    assert (movie.parsed.title, movie.parsed.year) == ("The Matrix", 1999)
    assert sorted(Path(s).name for s in movie.subtitles) == ["The.Matrix.zh.srt", "en.ass"]


def test_files_in_root_and_multi_movie_folder(tmp_path: Path) -> None:
    _touch(
        tmp_path,
        "Inception.2010.mkv",
        "Inception.2010.chs.srt",
        "合集/Alien.1979.mkv",
        "合集/Aliens.1986.mkv",
        "合集/Aliens.1986.srt",
    )
    result = _scan(tmp_path)
    assert _by_name(result) == {
        "Inception.2010": ["Inception.2010.mkv"],
        "Alien.1979": ["Alien.1979.mkv"],
        "Aliens.1986": ["Aliens.1986.mkv"],
    }
    aliens = next(m for m in result.movies if m.name == "Aliens.1986")
    assert [Path(s).name for s in aliens.subtitles] == ["Aliens.1986.srt"]
    alien = next(m for m in result.movies if m.name == "Alien.1979")
    assert alien.subtitles == []
    inception = next(m for m in result.movies if m.name == "Inception.2010")
    assert [Path(s).name for s in inception.subtitles] == ["Inception.2010.chs.srt"]


def test_folder_without_year_uses_file_year(tmp_path: Path) -> None:
    _touch(tmp_path, "盗梦空间/Inception.2010.1080p.mkv")
    movie = _scan(tmp_path).movies[0]
    assert (movie.parsed.title, movie.parsed.year) == ("Inception", 2010)


def test_disc_folders_are_one_movie(tmp_path: Path) -> None:
    _touch(
        tmp_path,
        "Heat (1995)/VIDEO_TS/VIDEO_TS.IFO",
        "Heat (1995)/VIDEO_TS/VTS_01_1.VOB",
        "Avatar (2009)/BDMV/index.bdmv",
        "Avatar (2009)/BDMV/STREAM/00001.m2ts",
    )
    names = _by_name(_scan(tmp_path))
    assert names == {"Heat (1995)": ["VIDEO_TS.IFO"], "Avatar (2009)": ["index.bdmv"]}


def test_skipped_folders(tmp_path: Path) -> None:
    _touch(
        tmp_path,
        "MAKEMKV/a.mkv",
        "makemkv2/b.mkv",
        "$RECYCLE.BIN/c.mkv",
        ".hidden/d.mkv",
        "Movie (2000)/extras/e.mkv",
        "Movie (2000)/Movie.mkv",
        "Other/Custom/f.mkv",
    )
    names = set(_by_name(_scan(tmp_path, skip=["MAKEMKV", "custom"])))
    assert names == {"makemkv2", "Movie (2000)"}


def test_missing_root_is_an_error_not_a_crash(tmp_path: Path) -> None:
    result = scan_paths(
        [str(tmp_path / "没有")],
        video_extensions=[".mkv"],
        subtitle_extensions=[".srt"],
        skip_folders=[],
    )
    assert result.movies == []
    assert len(result.errors) == 1 and "没有" in result.errors[0]


def test_extensions_are_case_insensitive(tmp_path: Path) -> None:
    _touch(tmp_path, "A (2001)/A.MKV")
    assert _by_name(_scan(tmp_path)) == {"A (2001)": ["A.MKV"]}
