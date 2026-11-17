from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mortis.api.app import create_app
from mortis.config.store import ConfigStore
from mortis.settings import Settings

LOCAL = "http://127.0.0.1"


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    return tmp_path / "data"


@pytest.fixture
def client(data_dir: Path) -> Iterator[TestClient]:
    with TestClient(create_app(Settings(data_dir=data_dir)), base_url=LOCAL) as opened:
        yield opened


def _available(client: TestClient, data_type: str) -> dict[str, tuple[bool, str | None]]:
    rows = client.get("/api/sources", params={"data_type": data_type}).json()
    return {row["id"]: (row["available"], row["reason"]) for row in rows}


def test_list_has_four_configurable_sources(client: TestClient) -> None:
    rows = client.get("/api/sources/builtin").json()
    assert [row["id"] for row in rows] == ["fanart", "tvdb", "imdb", "ratings_rt"]
    by_id = {row["id"]: row for row in rows}
    assert by_id["fanart"]["secrets"] == {"api_key": False, "client_key": False}
    assert by_id["fanart"]["reason"] == "Key 未填"
    assert by_id["tvdb"]["data_types"] == ["anime", "tv"]
    assert by_id["tvdb"]["secrets"] == {"api_key": False, "pin": False}
    assert by_id["imdb"]["settings"] == {"enabled": True, "refresh_days": 7}
    assert by_id["imdb"]["reason"] == "评分数据还没下载"
    assert by_id["ratings_rt"] == {
        "id": "ratings_rt",
        "name": "烂番茄",
        "data_types": ["anime", "movie", "tv"],
        "available": True,
        "reason": None,
        "settings": {"enabled": True},
        "secrets": {"mdblist_api_key": False},
        "provider": "Wikidata",
    }


def test_tmdb_has_no_separate_settings(client: TestClient) -> None:
    assert client.get("/api/sources/builtin/tmdb").status_code == 404
    assert client.put("/api/sources/builtin/tmdb", json={}).status_code == 404
    assert client.get("/api/sources/builtin/nope").status_code == 404


def test_secret_saved_without_echo_and_makes_source_available(
    client: TestClient, data_dir: Path
) -> None:
    response = client.put(
        "/api/sources/builtin/fanart", json={"secrets": {"api_key": "  fan-secret-1  "}}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["secrets"] == {"api_key": True, "client_key": False}
    assert body["available"] is True and body["reason"] is None
    assert "fan-secret-1" not in response.text
    assert "fan-secret-1" not in client.get("/api/sources/builtin").text
    assert ConfigStore(data_dir).load_secrets().sources["fanart"]["api_key"] == "fan-secret-1"
    assert _available(client, "movie")["fanart"] == (True, None)


def test_missing_secret_key_unchanged_and_empty_clears(client: TestClient, data_dir: Path) -> None:
    client.put("/api/sources/builtin/tvdb", json={"secrets": {"api_key": "k", "pin": "p"}})
    body = client.put("/api/sources/builtin/tvdb", json={"secrets": {"pin": ""}}).json()
    assert body["secrets"] == {"api_key": True, "pin": False}
    assert ConfigStore(data_dir).load_secrets().sources["tvdb"] == {"api_key": "k", "pin": ""}
    body = client.put("/api/sources/builtin/tvdb", json={"secrets": {"api_key": ""}}).json()
    assert body["available"] is False and body["reason"] == "Key 未填"


def test_secret_keys_of_other_sections_kept(client: TestClient, data_dir: Path) -> None:
    client.put("/api/secrets", json={"tmdb_api_key": "tmdb-1"})
    client.put("/api/sources/builtin/ratings_rt", json={"secrets": {"mdblist_api_key": "m"}})
    secrets = ConfigStore(data_dir).load_secrets()
    assert secrets.tmdb_api_key == "tmdb-1"
    assert client.get("/api/sources/builtin/ratings_rt").json()["provider"] == "MDBList"


def test_settings_merged_and_validated(client: TestClient, data_dir: Path) -> None:
    body = client.put("/api/sources/builtin/imdb", json={"settings": {"refresh_days": 30}}).json()
    assert body["settings"] == {"enabled": True, "refresh_days": 30}
    body = client.put("/api/sources/builtin/imdb", json={"settings": {"enabled": False}}).json()
    assert body["settings"] == {"enabled": False, "refresh_days": 30}
    assert body["reason"] == "已关闭"
    assert ConfigStore(data_dir).load_config().sources["imdb"] == {
        "enabled": False,
        "refresh_days": 30,
    }
    assert _available(client, "movie")["imdb"] == (False, "已关闭")

    bad = client.put("/api/sources/builtin/imdb", json={"settings": {"refresh_days": 0}})
    assert bad.status_code == 422
    assert "refresh_days" in bad.json()["detail"]
    assert client.get("/api/sources/builtin/imdb").json()["settings"]["refresh_days"] == 30


def test_unknown_keys_rejected_and_nothing_written(client: TestClient, data_dir: Path) -> None:
    response = client.put(
        "/api/sources/builtin/fanart",
        json={"secrets": {"api_key": "k", "token": "x"}},
    )
    assert response.status_code == 422
    assert "token" in response.json()["detail"]
    response = client.put(
        "/api/sources/builtin/ratings_rt",
        json={"settings": {"provider": "omdb"}, "secrets": {"mdblist_api_key": "m"}},
    )
    assert response.status_code == 422
    assert "provider" in response.json()["detail"]
    secrets = ConfigStore(data_dir).load_secrets().sources
    assert "fanart" not in secrets and "ratings_rt" not in secrets
    assert client.put("/api/sources/builtin/fanart", json={"other": 1}).status_code == 422


def test_bad_values_in_file_fall_back_to_defaults(client: TestClient, data_dir: Path) -> None:
    store = ConfigStore(data_dir)
    store.save_plugin("imdb", values={"enabled": True, "refresh_days": 999})
    assert client.get("/api/sources/builtin/imdb").json()["settings"]["refresh_days"] == 7
    body = client.put("/api/sources/builtin/imdb", json={"settings": {"enabled": False}}).json()
    assert body["settings"] == {"enabled": False, "refresh_days": 7}
