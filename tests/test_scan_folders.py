from pathlib import Path

from mortis.organize.scan import ScanResult, scan_folders


def _mkdir(root: Path, *relative: str) -> None:
    for rel in relative:
        (root / rel).mkdir(parents=True, exist_ok=True)


def _scan(root: Path, skip: list[str] | None = None) -> ScanResult:
    return scan_folders([str(root)], skip_folders=skip if skip is not None else [])


def test_each_subfolder_is_one_game(tmp_path: Path) -> None:
    _mkdir(tmp_path, "[KOMMIT] 游戏名 v1.02 汉化硬盘版", "千恋＊万花")
    result = _scan(tmp_path)
    assert [movie.name for movie in result.movies] == [
        "[KOMMIT] 游戏名 v1.02 汉化硬盘版",
        "千恋＊万花",
    ]
    first = result.movies[0]
    assert first.folder == str(tmp_path / "[KOMMIT] 游戏名 v1.02 汉化硬盘版")
    assert first.parsed.title == "游戏名"
    assert first.parsed.year is None
    assert first.parsed.search_titles == ("游戏名",)
    assert first.videos == [first.folder]
    assert first.subtitles == []
    assert result.errors == []


def test_does_not_descend_into_game_folders(tmp_path: Path) -> None:
    _mkdir(tmp_path, "游戏A/游戏A的子目录", "游戏B")
    (tmp_path / "游戏A" / "游戏A的子目录" / "data").mkdir()
    assert [movie.name for movie in _scan(tmp_path).movies] == ["游戏A", "游戏B"]


def test_skips_configured_and_hidden_folders(tmp_path: Path) -> None:
    _mkdir(
        tmp_path,
        "游戏A",
        "游戏B",
        "Saved Games",
        "SAVED GAMES",
        "$RECYCLE.BIN",
        ".hidden",
        "lost+found",
    )
    (tmp_path / "散装文件.txt").write_text("x", encoding="utf-8")
    names = [movie.name for movie in _scan(tmp_path, skip=["saved games"]).movies]
    assert names == ["游戏A", "游戏B"]


def test_full_path_skips_only_that_folder(tmp_path: Path) -> None:
    _mkdir(tmp_path, "A/同名", "B/同名", "游戏C")
    skip = [str(tmp_path / "游戏C").upper()]
    assert [movie.name for movie in _scan(tmp_path, skip=skip).movies] == ["A", "B"]

    _game(tmp_path / "A" / "同名", "a.exe", "a.dat")
    _game(tmp_path / "B" / "同名", "a.exe", "a.dat")
    result = scan_folders(
        [str(tmp_path)], skip_folders=[str(tmp_path / "A" / "同名") + "\\"], recursive=True
    )
    assert [movie.folder for movie in result.movies] == [str(tmp_path / "B" / "同名")]


def test_missing_root_is_an_error_not_a_crash(tmp_path: Path) -> None:
    result = scan_folders([str(tmp_path / "没有")], skip_folders=[])
    assert result.movies == []
    assert len(result.errors) == 1 and "没有" in result.errors[0]


def _game(folder: Path, *files: str) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    for name in files:
        (folder / name).write_bytes(b"x")


def test_recursive_finds_games_under_a_series_folder(tmp_path: Path) -> None:
    game = tmp_path / "炎孕系列" / "[KOMMIT] 炎孕 v1.0"
    _game(game, "炎孕.exe", "data.xp3")
    (game / "data").mkdir()
    (game / "data" / "extra.dat").write_bytes(b"x")
    result = scan_folders([str(tmp_path)], skip_folders=[], recursive=True)
    assert [movie.name for movie in result.movies] == ["[KOMMIT] 炎孕 v1.0"]
    found = result.movies[0]
    assert found.folder == str(game)
    assert found.parsed.title == "炎孕"
    assert found.videos == [str(game)]
    assert result.errors == []


def test_recursive_requires_exe_and_a_resource(tmp_path: Path) -> None:
    _game(tmp_path / "千恋万花", "千恋万花.exe", "readme.txt")
    _game(tmp_path / "只有程序", "a.exe")
    _game(tmp_path / "只有资源", "a.xp3")
    (tmp_path / "空文件夹").mkdir()
    result = scan_folders([str(tmp_path)], skip_folders=[], recursive=True)
    assert [movie.name for movie in result.movies] == ["千恋万花"]


def test_recursive_matches_extension_case_and_skips_hidden(tmp_path: Path) -> None:
    _game(tmp_path / "系列" / "作品", "RUN.EXE", "DATA.ARC")
    _game(tmp_path / ".hidden" / "隐藏游戏", "a.exe", "a.dat")
    _game(tmp_path / "Saved Games" / "跳过", "a.exe", "a.dat")
    result = scan_folders([str(tmp_path)], skip_folders=["saved games"], recursive=True)
    assert [movie.name for movie in result.movies] == ["作品"]
