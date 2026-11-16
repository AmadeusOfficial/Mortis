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


def test_missing_root_is_an_error_not_a_crash(tmp_path: Path) -> None:
    result = scan_folders([str(tmp_path / "没有")], skip_folders=[])
    assert result.movies == []
    assert len(result.errors) == 1 and "没有" in result.errors[0]
