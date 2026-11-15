import pytest

from mortis.organize.names import (
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


def test_mixed_cjk_and_latin_give_extra_search_titles() -> None:
    parsed = parse_name("黑客帝国.The.Matrix.1999")
    assert parsed.search_titles == ("黑客帝国 The Matrix", "黑客帝国", "The Matrix")
    assert parse_name("The.Matrix.1999").search_titles == ("The Matrix",)


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
