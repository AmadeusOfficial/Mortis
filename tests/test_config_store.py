import tomllib
from pathlib import Path

import pytest
from pydantic import ValidationError

from mortis.config.models import (
    SECRET_KEYS,
    AppConfig,
    FilesConfig,
    MovieScrapeConfig,
    NetworkConfig,
    Secrets,
    TasksConfig,
)
from mortis.config.store import ConfigError, ConfigStore

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_defaults_match_local_tmm() -> None:
    network = AppConfig().network
    assert (network.proxy_host, network.proxy_port, network.ignore_cert_errors) == (
        "127.0.0.1",
        7890,
        True,
    )
    assert SECRET_KEYS == ("tmdb_api_key", "proxy_username", "proxy_password")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("127.0.0.1", "127.0.0.1"),
        ("proxy.local", "proxy.local"),
        ("  my-proxy.example.com  ", "my-proxy.example.com"),
        ("[::1]", "[::1]"),
        ("[fe80::1]", "[fe80::1]"),
        ("", ""),
        ("   ", ""),
    ],
)
def test_proxy_host_accepts_hostnames_and_ips(raw: str, expected: str) -> None:
    assert NetworkConfig(proxy_host=raw).proxy_host == expected


@pytest.mark.parametrize(
    "raw",
    [
        "http://127.0.0.1",
        "socks5://proxy",
        "127.0.0.1:7890",
        "proxy/path",
        "user@proxy",
        "my proxy",
        "pro\txy",
        "::1",
        "fe80::1",
        "[::1]:7890",
        "[proxy]",
        "[::1",
        "::1]",
    ],
)
def test_proxy_host_rejects_malformed_values(raw: str) -> None:
    with pytest.raises(ValidationError, match="代理主机只填主机名或 IP"):
        NetworkConfig(proxy_host=raw)


def test_missing_files_give_defaults_without_creating(tmp_path: Path) -> None:
    store = ConfigStore(tmp_path / "data")
    assert store.load_config() == AppConfig()
    assert store.load_secrets() == Secrets()
    assert not store.config_path.exists()
    assert not store.secrets_path.exists()


def test_config_roundtrip(tmp_path: Path) -> None:
    store = ConfigStore(tmp_path / "data")
    config = AppConfig(
        network=NetworkConfig(proxy_host="", proxy_port=1080, ignore_cert_errors=False)
    )
    store.save_config(config)
    assert store.load_config() == config
    assert tomllib.loads(store.config_path.read_text(encoding="utf-8"))["network"] == {
        "proxy_host": "",
        "proxy_port": 1080,
        "ignore_cert_errors": False,
    }
    assert list(store.config_path.parent.glob("*.tmp")) == []


def test_secrets_live_in_their_own_file(tmp_path: Path) -> None:
    store = ConfigStore(tmp_path)
    store.save_config(AppConfig())
    store.save_secrets(Secrets(tmdb_api_key="k-123", proxy_password="p@ss"))
    assert store.load_secrets().tmdb_api_key == "k-123"
    assert "k-123" not in store.config_path.read_text(encoding="utf-8")
    assert store.secrets_path.name == "secrets.toml"
    assert store.config_path.name == "config.toml"


def test_broken_toml_names_the_file(tmp_path: Path) -> None:
    store = ConfigStore(tmp_path)
    store.config_path.write_text("[network\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="config.toml"):
        store.load_config()


def test_invalid_value_names_the_file(tmp_path: Path) -> None:
    store = ConfigStore(tmp_path)
    store.config_path.write_text("[network]\nproxy_port = 0\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="config.toml"):
        store.load_config()


def test_unknown_keys_are_ignored(tmp_path: Path) -> None:
    store = ConfigStore(tmp_path)
    store.config_path.write_text(
        '[network]\nproxy_host = "10.0.0.2"\nfuture = 1\n[later]\nx = 2\n', encoding="utf-8"
    )
    assert store.load_config().network.proxy_host == "10.0.0.2"


def test_proxy_host_is_stripped() -> None:
    assert NetworkConfig(proxy_host="  127.0.0.1 ").proxy_host == "127.0.0.1"


def test_examples_match_defaults() -> None:
    config = tomllib.loads((REPO_ROOT / "config.example.toml").read_text(encoding="utf-8"))
    secrets = tomllib.loads((REPO_ROOT / "secrets.example.toml").read_text(encoding="utf-8"))
    assert AppConfig.model_validate(config) == AppConfig()
    assert Secrets.model_validate(secrets) == Secrets()
    assert set(secrets) == set(SECRET_KEYS)


def test_non_utf8_file_becomes_config_error(tmp_path: Path) -> None:
    store = ConfigStore(tmp_path)
    store.config_path.write_bytes('[network]\nproxy_host = "中文"\n'.encode("gbk"))
    with pytest.raises(ConfigError, match="config.toml"):
        store.load_config()


def test_unreadable_file_becomes_config_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = ConfigStore(tmp_path)
    store.secrets_path.write_text('tmdb_api_key = "x"\n', encoding="utf-8")

    def deny(self: Path, *args: object, **kwargs: object) -> str:
        raise PermissionError("denied")

    monkeypatch.setattr(Path, "read_text", deny)
    with pytest.raises(ConfigError, match="secrets.toml"):
        store.load_secrets()


def test_validation_error_does_not_leak_values(tmp_path: Path) -> None:
    store = ConfigStore(tmp_path)
    store.secrets_path.write_text("proxy_password = 123456789\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="secrets.toml") as info:
        store.load_secrets()
    message = str(info.value)
    assert "123456789" not in message
    assert "proxy_password" in message


def test_failed_replace_keeps_original_and_no_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = ConfigStore(tmp_path)
    original = AppConfig(network=NetworkConfig(proxy_port=1111))
    store.save_config(original)
    before = store.config_path.read_bytes()

    def boom(src: object, dst: object) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr("mortis.config.store.os.replace", boom)
    with pytest.raises(OSError, match="replace failed"):
        store.save_config(AppConfig(network=NetworkConfig(proxy_port=2222)))
    assert store.config_path.read_bytes() == before
    assert list(tmp_path.glob("*.tmp")) == []


def test_new_sections_default_to_local_tmm() -> None:
    config = AppConfig()
    assert ".mkv" in config.files.video_extensions
    assert ".iso" in config.files.video_extensions
    assert ".srt" in config.files.subtitle_extensions
    movie = config.movie
    assert (movie.language, movie.certification_country) == ("zh-CN", "US")
    assert movie.include_adult is True
    assert movie.match_threshold == 0.75
    assert movie.skip_folders == ["MAKEMKV"]
    assert (movie.poster_size, movie.fanart_size) == ("w780", "original")
    assert movie.prefer_textless_fanart is True
    assert all(movie.images.model_dump().values())
    assert config.tasks.concurrency == 3
    assert config.tasks.keep_days == 30


def test_extensions_are_normalized() -> None:
    files = FilesConfig(video_extensions=[" MKV", ".mp4", "mkv", ""], subtitle_extensions=["srt"])
    assert files.video_extensions == [".mkv", ".mp4"]
    assert files.subtitle_extensions == [".srt"]


@pytest.mark.parametrize("bad", [["m kv"], ["*.mkv"], [], ["", " "]])
def test_extensions_reject_bad_values(bad: list[str]) -> None:
    with pytest.raises(ValidationError):
        FilesConfig(video_extensions=bad)


@pytest.mark.parametrize(
    "patch",
    [
        {"language": "zh"},
        {"certification_country": "usa"},
        {"match_threshold": 1.5},
        {"poster_size": "w9999"},
    ],
)
def test_movie_section_rejects_bad_values(patch: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        MovieScrapeConfig.model_validate(patch)


def test_skip_folders_are_cleaned() -> None:
    movie = MovieScrapeConfig(skip_folders=[" MAKEMKV ", "", "makemkv", "Extras"])
    assert movie.skip_folders == ["MAKEMKV", "Extras"]


def test_tasks_concurrency_bounds() -> None:
    with pytest.raises(ValidationError):
        TasksConfig(concurrency=0)
    with pytest.raises(ValidationError):
        TasksConfig(concurrency=9)


def test_save_section_keeps_other_sections_and_unknown_keys(tmp_path: Path) -> None:
    store = ConfigStore(tmp_path)
    store.config_path.write_text(
        "[network]\nproxy_port = 1080\nfuture = 1\n[later]\nx = 2\n", encoding="utf-8"
    )
    saved = store.save_section("movie", MovieScrapeConfig(language="en-US"))
    assert saved.movie.language == "en-US"
    assert saved.network.proxy_port == 1080
    raw = tomllib.loads(store.config_path.read_text(encoding="utf-8"))
    assert raw["network"] == {"proxy_port": 1080, "future": 1}
    assert raw["later"] == {"x": 2}
    assert raw["movie"]["language"] == "en-US"


def test_save_section_rejects_unknown_name(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        ConfigStore(tmp_path).save_section("nope", TasksConfig())
