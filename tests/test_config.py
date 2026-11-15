from pathlib import Path

import pytest
from pydantic import ValidationError

from mortis.config.models import MovieOutputConfig, MovieScrapeConfig
from mortis.config.store import ConfigStore


def test_output_defaults() -> None:
    output = MovieOutputConfig()
    assert output.write_after_scrape is True
    assert output.rename_after_scrape is False
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
            write_after_scrape=False,
            rename_after_scrape=True,
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
    assert loaded.rename_after_scrape is True
    assert loaded.nfo_names == ["filename"]
    assert loaded.image_style == "plain"
