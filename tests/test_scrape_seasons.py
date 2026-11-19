from typing import Any

from mortis.db.models import MediaFile
from mortis.scrape.tv import _absolute, _folder_season, _local_numbers, _resolve

KAIJI: dict[str, Any] = {
    "name": "赌博默示录",
    "original_name": "逆境無頼カイジ",
    "first_air_date": "2007-10-03",
    "seasons": [
        {"season_number": 0, "name": "特别篇", "air_date": "2008-01-01", "episode_count": 2},
        {"season_number": 1, "name": "赌博默示录", "air_date": "2007-10-03", "episode_count": 26},
        {"season_number": 2, "name": "赌博破戒录", "air_date": "2011-04-06", "episode_count": 26},
    ],
}


def test_sequel_folder_year_picks_that_season() -> None:
    assert _folder_season("赌博默示录：破戒录篇 (2011)", KAIJI) == 2


def test_sequel_folder_name_picks_that_season_without_year() -> None:
    assert _folder_season("赌博默示录：赌博破戒录", KAIJI) == 2


def test_show_folder_itself_is_not_a_season_even_with_other_year() -> None:
    assert _folder_season("赌博默示录 (2011)", KAIJI) is None
    assert _folder_season("赌博默示录 (2007)", KAIJI) is None


def test_absolute_numbers_follow_season_counts() -> None:
    assert _absolute(5, KAIJI) == (1, 5)
    assert _absolute(26, KAIJI) == (1, 26)
    assert _absolute(30, KAIJI) == (2, 4)
    assert _absolute(60, KAIJI) == (1, 60)


def test_resolve_keeps_explicit_seasons_and_maps_the_rest() -> None:
    local = {
        r"M:\动漫\赌博默示录：破戒录篇 (2011)\[DBD][赌博默示录：破戒录篇][01].mkv": (None, (1,)),
        r"M:\动漫\赌博默示录 (2007)\Kaiji.30.mkv": (None, (30,)),
        r"M:\动漫\赌博默示录 (2007)\Kaiji.03.mkv": (None, (3,)),
        r"M:\动漫\赌博默示录 (2007)\Season 1\Kaiji.S01E07.mkv": (1, (7,)),
    }
    assert list(_resolve(local, KAIJI).values()) == [(2, (1,)), (2, (4,)), (1, (3,)), (1, (7,))]


def test_four_digit_episodes_stay_distinct() -> None:
    shin: dict[str, Any] = {
        "name": "蜡笔小新",
        "first_air_date": "1992-04-13",
        "seasons": [{"season_number": 1, "air_date": "1992-04-13", "episode_count": 1500}],
    }
    folder = r"Z:\动漫\蜡笔小新 (1992)\Season 1"
    files = [
        MediaFile(path=rf"{folder}\蜡笔小新.S01E{n}.flv", file_kind="video")
        for n in (100, *range(1000, 1010))
    ]
    resolved = _resolve(_local_numbers(files), shin)
    assert sorted(numbers for _, numbers in resolved.values()) == [
        (100,),
        *((n,) for n in range(1000, 1010)),
    ]
