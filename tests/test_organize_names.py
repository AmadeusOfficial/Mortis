import pytest

from mortis.organize.names import (
    clean_game_name,
    parse_folder_name,
    parse_name,
    stack_key,
    strip_stack,
    subtitle_belongs_to,
)


@pytest.mark.parametrize(
    ("name", "title", "year"),
    [
        ("The.Matrix.1999.1080p.BluRay.x264-GROUP", "The Matrix", 1999),
        ("The Matrix (1999)", "The Matrix", 1999),
        ("Blade.Runner.2049.2017.2160p.UHD", "Blade Runner 2049", 2017),
        ("2001.A.Space.Odyssey.1968", "2001 A Space Odyssey", 1968),
        ("1917 (2019)", "1917", 2019),
        ("2012", "2012", None),
        ("[字幕组] 千与千寻 Spirited Away (2001) [1080p]", "千与千寻 Spirited Away", 2001),
        ("黑客帝国.The.Matrix.1999.BD1080P.中英字幕", "黑客帝国 The Matrix", 1999),
        ("Inception_2010_WEB-DL", "Inception", 2010),
        ("Amélie", "Amélie", None),
        ("Charlotte's Web 2006", "Charlotte's Web", 2006),
        ("Movie 1080p 2019", "Movie", 2019),
        ("霸王别姬 中英字幕", "霸王别姬", None),
    ],
)
def test_parse_name(name: str, title: str, year: int | None) -> None:
    parsed = parse_name(name)
    assert (parsed.title, parsed.year) == (title, year)


@pytest.mark.parametrize(
    ("name", "title", "year"),
    [
        ("[DBD-Raws][赌博默示录：破戒录篇][1080P][BDRip]", "赌博默示录:破戒录篇", None),
        ("[Nekomoe kissaten] Frieren [1080p]", "Frieren", None),
        ("[Group][Ascendance of a Bookworm][BDRip]", "Ascendance of a Bookworm", None),
        ("The.Matrix.1999.1080p.BluRay.x264-GROUP", "The Matrix", 1999),
    ],
)
def test_parse_name_drops_fansub_and_release_brackets(
    name: str, title: str, year: int | None
) -> None:
    parsed = parse_name(name)
    assert (parsed.title, parsed.year) == (title, year)


def test_mixed_cjk_and_latin_give_extra_search_titles() -> None:
    parsed = parse_name("黑客帝国.The.Matrix.1999")
    assert parsed.search_titles == ("黑客帝国 The Matrix", "黑客帝国", "The Matrix")
    assert parse_name("The.Matrix.1999").search_titles == ("The Matrix",)


def test_release_info_in_parentheses_is_dropped() -> None:
    parsed = parse_name(
        "[Seed-Raws] 劇場版 空の境界 - 第02章 殺人考察（前） (BD 1280x720 HEVC AACx2 [5.1+2.0])"
    )
    assert parsed.title == "劇場版 空の境界 - 第02章 殺人考察(前)"
    assert parsed.search_titles == (
        "劇場版 空の境界 - 第02章 殺人考察(前)",
        "劇場版 空の境界 殺人考察(前)",
    )


def test_chapter_number_kept_when_it_is_the_title() -> None:
    assert parse_name("Movie (1999)").title == "Movie"
    assert parse_name("第9区.2009").search_titles == ("第9区",)


@pytest.mark.parametrize(
    ("stem", "base", "part"),
    [
        ("Movie.cd1", "Movie", "1"),
        ("Movie - Part 2", "Movie", "2"),
        ("Movie[disc3]", "Movie", "3"),
        ("Movie.pt.b", "Movie", "b"),
        ("Movie.CD2", "Movie", "2"),
        ("Movie", "Movie", None),
        ("cd1", "cd1", None),
        ("Part 2 Movie", "Part 2 Movie", None),
    ],
)
def test_strip_stack(stem: str, base: str, part: str | None) -> None:
    assert strip_stack(stem) == (base, part)


def test_stack_key_ignores_case_and_trailing_separators() -> None:
    assert stack_key("Movie.CD1") == stack_key("movie - cd2") == "movie"


def test_subtitle_needs_separator_after_base() -> None:
    assert subtitle_belongs_to("Alien.1979.zh.srt", "Alien.1979")
    assert not subtitle_belongs_to("Aliens.1986.srt", "Alien")


def test_folder_name_falls_back_to_file_year() -> None:
    assert parse_folder_name("盗梦空间", "Inception.2010.cd1").title == "Inception"
    assert parse_folder_name("Heat (1995)", "heat").year == 1995
    assert parse_folder_name("热火", "heat").title == "热火"


@pytest.mark.parametrize(
    ("name", "title"),
    [
        ("[KOMMIT] 游戏名 v1.02 汉化硬盘版", "游戏名"),
        ("Dies irae ～Amantes amentes～", "Dies irae ～Amantes amentes～"),
        ("【汉化】サクラノ詩 -櫻の森の上を舞う- 完整版", "サクラノ詩 -櫻の森の上を舞う-"),
        ("[180126][ゆずソフト] 千恋＊万花 Ver.1.10", "千恋＊万花"),
        ("(18禁) 白 2 汉化版", "白 2"),
        ("CLANNAD ver1.10 简体中文", "CLANNAD"),
        ("Fate/stay night [Realta Nua] 免安装", "Fate/stay night"),
        ("初回限定版 俺の彼女", "俺の彼女"),
        ("[汉化组]", "[汉化组]"),
    ],
)
def test_clean_game_name(name: str, title: str) -> None:
    assert clean_game_name(name) == title
