import pytest

from mortis.rename.template import TOKENS, render

FILE_TEMPLATE = "${title}${- ,edition,}.${videoFormat}.${audioCodec}"
FOLDER_TEMPLATE = "${title} ${- ,edition,} (${year})"


def test_file_template_full() -> None:
    tokens = {"title": "战争之王", "videoFormat": "1080p", "audioCodec": "AC3"}
    assert render(FILE_TEMPLATE, tokens, space=".", colon="-") == "战争之王.1080p.AC3"


def test_file_template_without_formats() -> None:
    tokens = {"title": "战争之王", "videoFormat": None, "audioCodec": None}
    assert render(FILE_TEMPLATE, tokens, space=".", colon="-") == "战争之王"


def test_file_template_with_only_audio_codec() -> None:
    tokens = {"title": "战争之王", "audioCodec": "AC3"}
    assert render(FILE_TEMPLATE, tokens, space=".", colon="-") == "战争之王.AC3"


def test_folder_template_keeps_spaces() -> None:
    tokens = {"title": "战争之王", "year": "2005"}
    assert render(FOLDER_TEMPLATE, tokens, space="", colon="-") == "战争之王 (2005)"


def test_folder_template_with_edition() -> None:
    tokens = {"title": "战争之王", "year": "2005", "edition": "导演剪辑版"}
    assert render(FOLDER_TEMPLATE, tokens, space="", colon="-") == "战争之王 - 导演剪辑版 (2005)"


def test_english_file_name_uses_dots() -> None:
    tokens = {"title": "Lord of War", "videoFormat": "1080p", "audioCodec": "AC3"}
    assert render(FILE_TEMPLATE, tokens, space=".", colon="-") == "Lord.of.War.1080p.AC3"


def test_english_edition_drops_dash_next_to_dot() -> None:
    tokens = {"title": "Lord of War", "edition": "Director's Cut"}
    assert render("${title}${- ,edition,}", tokens, space=".", colon="-") == (
        "Lord.of.War.Director's.Cut"
    )
    assert render("${title}${- ,edition,}", tokens, space="_", colon="-") == (
        "Lord_of_War_Director's_Cut"
    )


def test_dash_inside_word_is_kept() -> None:
    tokens = {"title": "Spider-Man", "year": "2002"}
    assert render("${title} ${year}", tokens, space=".", colon="-") == "Spider-Man.2002"


def test_fullwidth_colon_becomes_dash() -> None:
    tokens = {"title": "星球大战：新希望", "year": "1977"}
    assert render(FOLDER_TEMPLATE, tokens, space="", colon="-") == "星球大战-新希望 (1977)"


def test_halfwidth_colon_strips_space_before_it() -> None:
    assert render("${title}", {"title": "Star Wars: A New Hope"}, space="", colon="-") == (
        "Star Wars- A New Hope"
    )


def test_empty_colon_removes_colon() -> None:
    assert render("${title}", {"title": "星球大战：新希望"}, space="", colon="") == "星球大战新希望"


def test_forbidden_characters_removed() -> None:
    tokens = {"title": 'a<b>c"d/e\\f|g?h*i'}
    assert render("${title}", tokens, space="", colon="-") == "abcdefghi"


def test_reserved_device_name_gets_underscore() -> None:
    assert render("${title}", {"title": "CON"}, space="", colon="-") == "CON_"
    assert render("${title}", {"title": "lpt9"}, space="", colon="-") == "lpt9_"


def test_whitespace_only_token_is_empty() -> None:
    tokens = {"title": "战争之王", "edition": "  ", "year": "2005"}
    assert render("${title}${- ,edition,}.${year}", tokens, space=".", colon="-") == "战争之王.2005"


def test_literal_text_kept() -> None:
    assert (
        render("电影 - ${title}", {"title": "战争之王"}, space="", colon="-") == "电影 - 战争之王"
    )


def test_dot_space_replaces_spaces_without_touching_dashes() -> None:
    tokens = {"title": "战争之王", "year": "2005"}
    assert render(FOLDER_TEMPLATE, tokens, space=".", colon="-") == "战争之王.(2005)"
    assert render("${title} - ${year}", tokens, space=".", colon="-") == "战争之王.2005"


def test_trailing_separators_stripped() -> None:
    assert render("${title}.", {"title": "战争之王"}, space="", colon="-") == "战争之王"
    assert render("${title} - ", {"title": "战争之王"}, space="", colon="-") == "战争之王"


@pytest.mark.parametrize(
    "template",
    ["${title,edition}", "${title", "${1title}", "${}"],
)
def test_bad_template_raises(template: str) -> None:
    with pytest.raises(ValueError, match="模板写法不对"):
        render(template, {"title": "战争之王"}, space="", colon="-")


def test_empty_result_raises() -> None:
    with pytest.raises(ValueError, match="模板生成的名字为空"):
        render("${year}", {}, space="", colon="-")
    with pytest.raises(ValueError, match="模板生成的名字为空"):
        render(" .-_", {}, space="", colon="-")


def test_unknown_token_renders_empty() -> None:
    assert render("${title}${unknown}", {"title": "战争之王"}, space="", colon="-") == "战争之王"


def test_tokens_export() -> None:
    assert TOKENS == (
        "title",
        "originalTitle",
        "year",
        "edition",
        "videoFormat",
        "audioCodec",
        "tmdb",
        "imdb",
    )


def test_plex_tmdb_marker() -> None:
    template = "${title} (${year}) ${{tmdb-,tmdb,}}"
    tokens = {"title": "低俗小说", "year": "1994", "tmdb": "680"}
    assert render(template, tokens, space="", colon="-") == "低俗小说 (1994) {tmdb-680}"
    tokens["tmdb"] = None
    assert render(template, tokens, space="", colon="-") == "低俗小说 (1994)"
