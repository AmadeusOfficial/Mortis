from pathlib import Path

import pytest
from pydantic import ValidationError

from mortis.config.models import (
    MovieOutputConfig,
    MovieScrapeConfig,
    TvImages,
    TvOutputConfig,
    TvScrapeConfig,
)
from mortis.config.store import ConfigStore


def test_output_defaults() -> None:
    output = MovieOutputConfig()
    assert output.organize_after_scrape is True
    assert output.organize_rename is False
    assert output.organize_write_files is True
    assert output.nfo_names == ["movie", "filename"]
    assert output.image_style == "filename"
    assert output.collection_image_names is True
    assert output.actor_images_folder == ".actors"
    assert output.folder_template == "${title} ${- ,edition,} (${year})"
    assert output.file_template == "${title}${- ,edition,}.${videoFormat}.${audioCodec}"
    assert output.folder_space == ""
    assert output.file_space == "."
    assert output.colon_replacement == "-"
    assert output.first_char_number == "#"
    assert output.nfo_write_lockdata is False
    assert output.nfo_single_studio is False


@pytest.mark.parametrize(
    ("old", "expected"),
    [
        ({"write_after_scrape": True, "rename_after_scrape": False}, (True, False, True)),
        ({"write_after_scrape": False, "rename_after_scrape": False}, (False, False, True)),
        ({"write_after_scrape": False, "rename_after_scrape": True}, (True, True, False)),
        ({"write_after_scrape": True, "rename_after_scrape": True}, (True, True, True)),
    ],
)
def test_old_switches_become_organize(old: dict[str, bool], expected: tuple[bool, ...]) -> None:
    output = MovieOutputConfig.model_validate(old)
    assert (
        output.organize_after_scrape,
        output.organize_rename,
        output.organize_write_files,
    ) == expected


def test_movie_output_has_defaults() -> None:
    assert MovieScrapeConfig().output == MovieOutputConfig()


def test_nfo_names_must_not_be_empty() -> None:
    with pytest.raises(ValidationError, match="NFO 文件名至少选一个"):
        MovieOutputConfig(nfo_names=[])


def test_nfo_names_are_deduped_keeping_order() -> None:
    output = MovieOutputConfig(nfo_names=["filename", "movie", "filename"])
    assert output.nfo_names == ["filename", "movie"]


@pytest.mark.parametrize("bad", ["movie/poster", "sub/actors", ".actors:1", 'a"b'])
def test_actor_images_folder_rejects_paths(bad: str) -> None:
    with pytest.raises(ValidationError, match="演员头像目录只填一个目录名"):
        MovieOutputConfig(actor_images_folder=bad)


def test_actor_images_folder_is_stripped() -> None:
    assert MovieOutputConfig(actor_images_folder="  .actors ").actor_images_folder == ".actors"


@pytest.mark.parametrize("bad", ["sub/dir", "sub\\dir"])
def test_template_rejects_path_separators(bad: str) -> None:
    with pytest.raises(ValidationError, match="模板里不能有路径分隔符"):
        MovieOutputConfig(folder_template=bad)


def test_templates_must_not_be_empty() -> None:
    with pytest.raises(ValidationError, match="模板不能为空"):
        MovieOutputConfig(file_template="   ")


@pytest.mark.parametrize("bad", [":", "?", "*", "|", "abcd"])
def test_replacement_rejects_bad_chars(bad: str) -> None:
    with pytest.raises(ValidationError, match="替换字符不能是文件名里不允许的字符"):
        MovieOutputConfig(colon_replacement=bad)


def test_unknown_nfo_names_value_is_rejected() -> None:
    with pytest.raises(ValidationError):
        MovieOutputConfig(nfo_names=["poster"])


def test_output_roundtrip_through_store(tmp_path: Path) -> None:
    store = ConfigStore(tmp_path / "data")
    movie = MovieScrapeConfig(
        output=MovieOutputConfig(
            organize_after_scrape=False,
            organize_rename=True,
            organize_write_files=False,
            nfo_names=["filename"],
            image_style="plain",
            actor_images_folder="actors",
            folder_template="${title} (${year})",
            file_template="${title}.${videoFormat}",
            folder_space="-",
            file_space="_",
            colon_replacement="：",
            first_char_number="0",
            nfo_write_lockdata=True,
            nfo_single_studio=True,
        )
    )
    store.save_section("movie", movie)
    loaded = store.load_config().movie.output
    assert loaded == movie.output
    assert loaded.organize_rename is True
    assert loaded.nfo_names == ["filename"]
    assert loaded.image_style == "plain"


def test_tv_defaults() -> None:
    tv = TvScrapeConfig()
    assert tv.language == "zh-CN"
    assert tv.certification_country == "US"
    assert tv.include_adult is True
    assert tv.match_threshold == 0.75
    assert tv.skip_folders == ["MAKEMKV"]
    assert tv.poster_size == "w780"
    assert tv.fanart_size == "original"
    assert tv.still_size == "w300"
    assert tv.prefer_textless_fanart is True
    assert tv.show_missing_episodes is True
    assert tv.show_missing_specials is True
    assert tv.images == TvImages(
        poster=True,
        fanart=True,
        clearlogo=True,
        keyart=True,
        season_poster=True,
        episode_thumb=True,
        actor_images=False,
    )
    assert tv.output == TvOutputConfig(
        organize_after_scrape=True,
        organize_rename=True,
        organize_write_files=True,
        show_template="${showTitle} (${showYear})",
        season_template="Season ${seasonNr}",
        file_template="${showTitle}.S${seasonNr2}E${episodeNr2}.${title}",
        show_space="",
        season_space="",
        file_space=".",
        colon_replacement=" ",
        first_char_number="#",
        nfo_write_lockdata=False,
        nfo_single_studio=False,
    )


@pytest.mark.parametrize("field", ["show_template", "season_template", "file_template"])
def test_tv_templates_must_not_be_empty(field: str) -> None:
    with pytest.raises(ValidationError, match="模板不能为空"):
        TvOutputConfig(**{field: "  "})


@pytest.mark.parametrize("field", ["show_template", "season_template", "file_template"])
@pytest.mark.parametrize("bad", ["sub/dir", "sub\\dir"])
def test_tv_template_rejects_path_separators(field: str, bad: str) -> None:
    with pytest.raises(ValidationError, match="模板里不能有路径分隔符"):
        TvOutputConfig(**{field: bad})


def test_tv_roundtrip_through_store(tmp_path: Path) -> None:
    store = ConfigStore(tmp_path / "data")
    tv = TvScrapeConfig(
        language="ja-JP",
        match_threshold=0.6,
        still_size="original",
        images=TvImages(actor_images=True, episode_thumb=False),
        output=TvOutputConfig(organize_rename=False, season_template="第 ${seasonNr} 季"),
    )
    store.save_section("tv", tv)
    assert store.load_config().tv == tv
