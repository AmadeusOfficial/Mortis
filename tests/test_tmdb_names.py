from mortis.config.models import MovieScrapeConfig
from mortis.sources.tmdb.movie import movie_candidates
from mortis.sources.tmdb.names import country_name, language_name
from tests.fakes import load_fixture


def test_country_name_known_codes() -> None:
    assert country_name("US") == "美国"
    assert country_name("FR") == "法国"
    assert country_name("CN") == "中国大陆"
    assert country_name("HK") == "中国香港"
    assert country_name("JP") == "日本"
    assert country_name("SU") == "苏联"


def test_country_name_normalizes_case() -> None:
    assert country_name("us") == "美国"
    assert country_name("Us") == "美国"


def test_country_name_unknown_code() -> None:
    assert country_name("zz") == "ZZ"
    assert country_name("qq") == "QQ"


def test_country_name_empty() -> None:
    assert country_name(None) is None
    assert country_name("") is None


def test_language_name_known_codes() -> None:
    assert language_name("en") == "英语"
    assert language_name("zh") == "汉语普通话"
    assert language_name("cn") == "粤语"
    assert language_name("ar") == "阿拉伯语"
    assert language_name("xx") == "无对白"


def test_language_name_normalizes_case() -> None:
    assert language_name("EN") == "英语"
    assert language_name("En") == "英语"
    assert language_name("CN") == "粤语"


def test_language_name_unknown_code() -> None:
    assert language_name("qq") == "qq"


def test_language_name_empty() -> None:
    assert language_name(None) is None
    assert language_name("") is None


def test_movie_candidates_writes_chinese_names() -> None:
    c = movie_candidates(load_fixture("movie_603.json"), MovieScrapeConfig())
    assert c["countries"] == ["美国"]
    assert c["spoken_languages"] == ["英语"]
