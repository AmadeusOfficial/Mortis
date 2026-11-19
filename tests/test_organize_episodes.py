import pytest

from mortis.organize.episodes import (
    EpisodeNumber,
    is_extra,
    parse_episode,
    season_of_folder,
)

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


BARE_CASES = [
    ("Durarara!!.S01.02", None, EpisodeNumber(1, (2,))),
    ("Durarara!!×2.Ten.S02.13", None, EpisodeNumber(2, (13,))),
    ("Show S02 13", None, EpisodeNumber(2, (13,))),
    ("Show.S2-13", None, EpisodeNumber(2, (13,))),
    ("Show.S2_13", None, EpisodeNumber(2, (13,))),
    ("Show.S02.13.1080p", None, EpisodeNumber(2, (13,))),
    ("Durarara!!×2.Ketsu.S02.19.5.1080px265_flac", None, EpisodeNumber(2, (19,))),
    ("Durarara!!.S01.12.5", None, EpisodeNumber(1, (12,))),
    # S01E02 优先于裸集号。
    ("Show.S01E02.720p", None, EpisodeNumber(1, (2,))),
    # 年份、分辨率、x264 之类不是集号。
    ("Show.2019.1080p", None, None),
    ("Show.S2019", None, None),
]


@pytest.mark.parametrize(("stem", "season_folder", "expected"), BARE_CASES)
def test_season_with_bare_episode(
    stem: str, season_folder: str | None, expected: EpisodeNumber | None
) -> None:
    assert parse_episode(stem, season_folder) == expected


LOOSE_CASES = [
    (
        "[DBD-Raws][赌博默示录：破戒录篇][01][1080P][BDRip][HEVC-10bit][FLAC]",
        "Season 1",
        EpisodeNumber(1, (1,)),
    ),
    (
        "[DBD-Raws][赌博默示录：破戒录篇][01][1080P][BDRip][HEVC-10bit][FLAC]",
        None,
        EpisodeNumber(None, (1,)),
    ),
    (
        "[UHA-WINGS][FX Senshi Kurumi-chan][06][1080p HEVC][CHS_JP]",
        "Season 1",
        EpisodeNumber(1, (6,)),
    ),
    ("[UHA-WINGS][FX Senshi Kurumi-chan][06][1080p HEVC][CHS_JP]", None, EpisodeNumber(None, (6,))),
    ("[Group][Title][06v2][1080p]", None, EpisodeNumber(None, (6,))),
    ("[Title][12END]", None, EpisodeNumber(None, (12,))),
    ("[Moozzi2] Space Dandy - 05 (BD 1920x1080 x.264 Flac)", None, EpisodeNumber(None, (5,))),
    ("Title - 01 [1080p]", None, EpisodeNumber(None, (1,))),
    ("漆黑的子弹.01", None, EpisodeNumber(None, (1,))),
    ("Steins;Gate.01起源与终结的序章", None, EpisodeNumber(None, (1,))),
    (
        "Ping_Pong_The_Animation.01][BDRIP][HEVC_FLACx2_THDx2_SUPx3]",
        None,
        EpisodeNumber(None, (1,)),
    ),
    ("Ping_Pong_The_Animation.01][BDRIP][HEVC_FLAC_THD_SUP]", None, EpisodeNumber(None, (1,))),
    # 排除项。
    ("Show.2019.1080p", None, None),
    ("Show.2019", None, None),
    ("Show [1920x1080]", None, None),
    ("Show 1080p", None, None),
    ("Show.720p", None, None),
    ("Show x264", None, None),
    ("Show x265", None, None),
    ("Show h264", None, None),
    ("Show.10bit", None, None),
    ("Show.8bit", None, None),
    ("Show FLACx2", None, None),
    ("Show 2Audio", None, None),
    ("Show 5.1", None, None),
    ("Show v2", None, None),
    ("Show x.264", None, None),
    ("Space Dandy [SP08] Original Film - 73", "EXTRA", None),
]


@pytest.mark.parametrize(("stem", "season_folder", "expected"), LOOSE_CASES)
def test_loose_episode_number(
    stem: str, season_folder: str | None, expected: EpisodeNumber | None
) -> None:
    assert parse_episode(stem, season_folder) == expected


SPECIAL_CASES = [
    ("Steins;Gate0.SP.同素异形的情人守护神", "Season 1", EpisodeNumber(0, (1,))),
    ("エルフェンリート.SP", "Season 1", EpisodeNumber(0, (1,))),
    ("[Group][Title][OVA02][1080p]", None, EpisodeNumber(0, (2,))),
    ("[Group][Title][OVA][1080p]", None, EpisodeNumber(0, (1,))),
    ("[Group][Title][OAD2][1080p]", None, EpisodeNumber(0, (2,))),
    ("[Group][Title][OAD][1080p]", None, EpisodeNumber(0, (1,))),
    ("[Group][Title][特典][1080p]", None, EpisodeNumber(0, (1,))),
    ("某剧 SP01", "Specials", EpisodeNumber(0, (1,))),
]


@pytest.mark.parametrize(("stem", "season_folder", "expected"), SPECIAL_CASES)
def test_specials_use_season_zero(
    stem: str, season_folder: str | None, expected: EpisodeNumber | None
) -> None:
    assert parse_episode(stem, season_folder) == expected


# 长寿番的四位集号不能被截成三位，也不能丢掉。
LONG_CASES = [
    ("蜡笔小新.S01E1100.标题", "Season 1", EpisodeNumber(1, (1100,))),
    ("蜡笔小新 S01E1000", None, EpisodeNumber(1, (1000,))),
    ("Show.S01E1099E1100", None, EpisodeNumber(1, (1099, 1100))),
    ("Show.S01E1000-1002", None, EpisodeNumber(1, (1000, 1001, 1002))),
    ("Show.S01E999-E1001", None, EpisodeNumber(1, (999, 1000, 1001))),
    ("Show.1x1100", None, EpisodeNumber(1, (1100,))),
    ("Show E1100", None, EpisodeNumber(None, (1100,))),
    ("Show EP1100", None, EpisodeNumber(None, (1100,))),
    ("Show.E1100E1101", None, EpisodeNumber(None, (1100, 1101))),
    ("Show - 1100 [1080p]", None, EpisodeNumber(None, (1100,))),
    ("Show.1100", None, EpisodeNumber(None, (1100,))),
    ("[Group][Show][1100][1080P]", None, EpisodeNumber(None, (1100,))),
    ("[Group][Show][1100v2]", None, EpisodeNumber(None, (1100,))),
    ("Show.S01.1100", None, EpisodeNumber(1, (1100,))),
    ("Show 第1100话", "Season 1", EpisodeNumber(1, (1100,))),
    # 五位数不是集号，也不能被截成四位。
    ("Show.S01E12345", None, None),
    ("Show E12345", None, None),
    # 放宽到四位后，年份和分辨率仍然不是集号。
    ("Show.2019", None, None),
    ("Show - 2019", None, None),
    ("[Group][Show][2019]", None, None),
    ("Show.S01.2019", None, None),
    ("Show.1080", None, None),
    ("[Group][Show][2160]", None, None),
    ("Show.S01E05.2019.1080p", None, EpisodeNumber(1, (5,))),
]


@pytest.mark.parametrize(("stem", "season_folder", "expected"), LONG_CASES)
def test_four_digit_episode_numbers(
    stem: str, season_folder: str | None, expected: EpisodeNumber | None
) -> None:
    assert parse_episode(stem, season_folder) == expected


EXTRA_STEM_CASES = [
    ("[EMD][Jormungand -Perfect Order-][ED2][X264_AAC][1280X720]", None, True),
    ("[EMD][Jormungand -Perfect Order-][ED][X264_AAC][1280X720]", None, True),
    ("[EMD][Jormungand -Perfect Order-][OP][X264_AAC][1280X720]", None, True),
    ("[EMD][Jormungand -Perfect Order-][OP1][X264_AAC][1280X720]", None, True),
    ("ID_INVADED.NCOP][1080p][BDrip][HEVC]", None, True),
    ("ID_INVADED.NCED][1080p][BDrip][HEVC]", None, True),
    ("Baccano! NCED", None, True),
    ("[LowPower-Raws] Devilman Crybaby - NCOP (BD 1080P x264 FLAC)", None, True),
    ("[DBD-Raws][漆黑的子弹][NCOP1][1080P][BDRip][HEVC-10bit][FLAC]", None, True),
    ("[DBD-Raws][漆黑的子弹][NCED][1080P][BDRip][HEVC-10bit][FLAC]", None, True),
    ("[DBD-Raws][漆黑的子弹][PV1][1080P][BDRip][HEVC-10bit][FLAC]", None, True),
    ("[Moozzi2] Space Dandy [SP05] TV-CM - 01 (BD 1920x1080 x.264 Flac)", None, True),
    ("[Moozzi2] Space Dandy [SP05] CM [ S2 ] (BD 1920x1080 x.264 Flac)", None, True),
    ("[Moozzi2] Space Dandy [SP04] CM01 (BD 1920x1080 x.264 Flac)", None, True),
    ("[Moozzi2] Space Dandy [SP04] Teaser PV (BD 1920x1080 x.264 Flac)", None, True),
    ("[Moozzi2] Space Dandy [SP09] Trailer (BD x.264 Flac)", None, True),
    ("[Moozzi2] Space Dandy [SP09] SPOT (BD x.264 Flac)", None, True),
    ("[A.I.R.nesSub][Ping_Pong_The_Animation][Vol.1][Menu][BDRIP][HEVC_FLAC]", None, True),
    ("[Group][Title][Preview][1080p]", None, True),
    ("[Group][Title][Opening][1080p]", None, True),
    # 字母开头的单词不算花絮。
    ("[Group][Title][OPPO][1080p]", None, False),
    ("Edward Scissorhands.01", None, False),
    ("摇曳露营△.S03E05.篝火与牛肉节", None, False),
    (
        "[DBD-Raws][JOJO的奇妙冒险 黄金之风][menu][01][1080P][BDRip][HEVC-10bit][FLAC]",
        None,
        True,
    ),
]


@pytest.mark.parametrize(("stem", "folder", "expected"), EXTRA_STEM_CASES)
def test_is_extra_stem(stem: str, folder: str | None, expected: bool) -> None:
    assert is_extra(stem, folder) is expected


EXTRA_FOLDER_CASES = [
    ("Whatever 01", "extras", True),
    ("Whatever 01", "Extras", True),
    ("Whatever 01", "EXTRA", True),
    ("Whatever 01", "extra", True),
    ("Whatever 01", "bonus", True),
    ("Whatever 01", "Bonus", True),
    ("Whatever 01", "menu", True),
    ("Whatever 01", "特典", True),
    ("Whatever 01", "映像特典", True),
    ("Whatever 01", "cd", True),
    ("Whatever 01", "scans", True),
    ("Whatever 01", "fonts", True),
    # 特别篇目录不是花絮；带季集号的正片标题里有 Opening 也不是。
    ("Whatever 01", "sps", False),
    ("Show.S01E01.Opening.Night", "Season 1", False),
    ("Whatever 01", "Specials", False),
    ("Whatever 01", "SPs", False),
    ("Whatever 01", "Season 1", False),
    ("Whatever 01", None, False),
    (
        "[Moozzi2] Space Dandy [SP08] Original Film - 73 (BD 1920x1080 x.264 Flac)",
        "EXTRA",
        True,
    ),
    (
        "[DBD-Raws][JOJO的奇妙冒险 黄金之风][menu][01][1080P][BDRip][HEVC-10bit][FLAC]",
        "menu",
        True,
    ),
    ("[Moozzi2] Space Dandy [SP01] - 05 (BD 1920x1080 x.264 Flac)", "Season 1", False),
]


@pytest.mark.parametrize(("stem", "folder", "expected"), EXTRA_FOLDER_CASES)
def test_is_extra_folder(stem: str, folder: str | None, expected: bool) -> None:
    assert is_extra(stem, folder) is expected


EXTRA_PARSE_CASES = [
    ("[EMD][Jormungand -Perfect Order-][ED2][X264_AAC][1280X720]", None),
    ("ID_INVADED.NCOP][1080p][BDrip][HEVC]", None),
    ("Baccano! NCED", "bonus"),
    (
        "[DBD-Raws][JOJO的奇妙冒险 黄金之风][menu][01][1080P][BDRip][HEVC-10bit][FLAC]",
        "menu",
    ),
    (
        "[Moozzi2] Space Dandy [SP08] Original Film - 73 (BD 1920x1080 x.264 Flac)",
        "EXTRA",
    ),
    ("[LowPower-Raws] Devilman Crybaby - NCOP (BD 1080P x264 FLAC)", None),
]


@pytest.mark.parametrize(("stem", "folder"), EXTRA_PARSE_CASES)
def test_parse_episode_skips_extras(stem: str, folder: str | None) -> None:
    assert parse_episode(stem, folder) is None
