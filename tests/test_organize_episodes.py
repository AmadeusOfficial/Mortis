import pytest

from mortis.organize.episodes import EpisodeNumber, parse_episode, season_of_folder

CASES = [
    (
        "毒枭：墨西哥.Narcos.Mexico.S01E01.中英字幕.BD.1080P-人人影视",
        "S01",
        EpisodeNumber(1, (1,)),
    ),
    (
        "毒枭：墨西哥.Narcos.Mexico.S02E10.end.中英字幕.WEB.1080P-人人影视",
        "S02",
        EpisodeNumber(2, (10,)),
    ),
    ("Narcos.Mexico.S03E10.1080p", "S03", EpisodeNumber(3, (10,))),
    (
        "The.Boys.S02E03.Over.the.Hill.2160p.AMZN.WEB-DL.DDP5.1.HDR.HEVC-ColorTV",
        "Season 2",
        EpisodeNumber(2, (3,)),
    ),
    ("[字幕组] 某剧 第05话 [1080p]", "Season 2", EpisodeNumber(2, (5,))),
    ("某剧 第12集", "第一季", EpisodeNumber(1, (12,))),
    ("某剧 EP03", "毒枭", EpisodeNumber(None, (3,))),
    ("Show.S01E01E02", None, EpisodeNumber(1, (1, 2))),
    ("Show.S01E01-E02", None, EpisodeNumber(1, (1, 2))),
    ("Show.1x02.720p", None, EpisodeNumber(1, (2,))),
    ("Show [1280x720] E05", None, EpisodeNumber(None, (5,))),
    ("Movie.2019.1080p", None, None),
    ("Movie.x264", None, None),
    (
        "乐队我做东.S02E01.第1期：马东臧鸿飞组饭局",
        "Season 2",
        EpisodeNumber(2, (1,)),
    ),
    ("某剧 SP01", "Specials", EpisodeNumber(0, (1,))),
]


@pytest.mark.parametrize(("stem", "season_folder", "expected"), CASES)
def test_parse_episode(
    stem: str, season_folder: str | None, expected: EpisodeNumber | None
) -> None:
    assert parse_episode(stem, season_folder) == expected


@pytest.mark.parametrize(
    ("stem", "expected"),
    [
        ("Show.s1e2", EpisodeNumber(1, (2,))),
        ("Show.S01.E02", EpisodeNumber(1, (2,))),
        ("Show.S01 E02", EpisodeNumber(1, (2,))),
        ("Show.S01_E02", EpisodeNumber(1, (2,))),
        ("Show.01x02", EpisodeNumber(1, (2,))),
        ("Show.S01E01E02E03", EpisodeNumber(1, (1, 2, 3))),
        ("Show.S01E01-02", EpisodeNumber(1, (1, 2))),
        ("Show.Ep.02", EpisodeNumber(None, (2,))),
        ("Show.E01E02", EpisodeNumber(None, (1, 2))),
        ("Show.E05", EpisodeNumber(None, (5,))),
        ("Show.第02話", EpisodeNumber(None, (2,))),
        ("Show.1920x1080", None),
        ("Show.2160p", None),
        ("Show.4k", None),
        ("Show.x265", None),
        ("Show.h.265", None),
    ],
)
def test_more_stem_forms(stem: str, expected: EpisodeNumber | None) -> None:
    assert parse_episode(stem, None) == expected


def test_file_season_wins_over_folder() -> None:
    assert parse_episode("Show.S03E04", "Season 1") == EpisodeNumber(3, (4,))


def test_full_width_digits_are_normalized() -> None:
    assert parse_episode("剧集 Ｓ０１Ｅ０２", None) == EpisodeNumber(1, (2,))
    assert season_of_folder("第３季") == 3


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("S01", 1),
        ("s2", 2),
        ("Season 1", 1),
        ("Season 01", 1),
        ("Season.1", 1),
        ("第3季", 3),
        ("第十二季", 12),
        ("第十季", 10),
        ("第二十季", 20),
        ("Specials", 0),
        ("Special", 0),
        ("SP", 0),
        ("特别篇", 0),
        ("  S01  ", 1),
        ("毒枭：墨西哥", None),
        ("S01 某剧", None),
        ("2019", None),
    ],
)
def test_season_of_folder(name: str, expected: int | None) -> None:
    assert season_of_folder(name) == expected
