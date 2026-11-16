import textwrap
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx2 as httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from mortis.api.app import create_app
from mortis.db.models import DataType, SourceOrder, TypeField
from mortis.plugin_api import SourceAuthError
from mortis.plugins.host import PluginHost, mask_url
from mortis.plugins.registry import PluginRegistry
from mortis.settings import Settings

LOCAL = "http://127.0.0.1"

DATA_TYPE = """
from mortis.plugin_api import DataTypePlugin, DataTypeSpec, FieldSpec

PLUGIN = DataTypePlugin(
    spec=DataTypeSpec(
        id="doujin",
        name="同人游戏",
        structure="item",
        fields=[
            FieldSpec(field_key="title", label="名称", value_type="text",
                      stored_in="items_column", column_name="title", detail_section="基本信息"),
            FieldSpec(field_key="engine", label="引擎", value_type="text",
                      stored_in="fields", detail_section="基本信息"),
        ],
    ),
    default_orders={"": ["alpha", "beta"], "engine": ["beta"]},
)
"""

SOURCE = """
from pydantic import BaseModel

from mortis.plugin_api import Hit, OAuth, SourcePlugin


class Secrets(BaseModel):
    app_id: str = ""
    app_secret: str = ""
    access_token: str = ""
    refresh_token: str = ""
    expires_at: int = 0


class Settings(BaseModel):
    limit: int = 5


class Alpha(SourcePlugin):
    id = "alpha"
    name = "Alpha"
    data_type = "doujin"
    provider = "alpha"
    Settings = Settings
    Secrets = Secrets
    oauth = OAuth(authorize_url="https://alpha.test/oauth/authorize",
                  token_url="https://alpha.test/oauth/token")
    requests_per_second = 0

    def disabled_reason(self, host):
        return None if host.access_token() else "没登录"

    def search(self, host, title):
        return [Hit(external_id="1", title=title)]

    def fetch(self, host, external_id):
        return host.get_json(f"https://alpha.test/item/{external_id}")

    def candidates(self, raw):
        return {"title": raw.get("name")}


PLUGIN = Alpha()
"""


def _write(folder: Path, plugin_id: str, kind: str, code: str) -> None:
    target = folder / plugin_id
    target.mkdir(parents=True, exist_ok=True)
    (target / "plugin.toml").write_text(
        f'id = "{plugin_id}"\nname = "{plugin_id}"\nversion = "1.0"\nkind = "{kind}"\n',
        encoding="utf-8",
    )
    (target / "__init__.py").write_text(textwrap.dedent(code), encoding="utf-8")


@pytest.fixture
def folders(tmp_path: Path) -> tuple[Path, Path]:
    builtin = tmp_path / "builtin"
    data = tmp_path / "data"
    _write(builtin, "doujin", "data_type", DATA_TYPE)
    _write(builtin, "alpha", "source", SOURCE)
    return builtin, data


class FakeSite:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.token_responses: list[dict] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path == "/oauth/token":
            return httpx.Response(200, json=self.token_responses.pop(0))
        if request.url.path == "/item/401":
            return httpx.Response(401, json={})
        return httpx.Response(200, json={"name": "条目"})


@pytest.fixture
def site() -> FakeSite:
    return FakeSite()


@pytest.fixture
def client(folders: tuple[Path, Path], site: FakeSite):
    builtin, data = folders
    app = create_app(
        Settings(data_dir=data, plugins_dir=builtin),
        http_transport=httpx.MockTransport(site.handler),
        start_worker=False,
    )
    with TestClient(app, base_url=LOCAL) as opened:
        yield opened


def test_loads_data_type_and_seeds_orders(client: TestClient) -> None:
    factory = client.app.state.session_factory
    with factory() as session:
        row = session.get(DataType, "doujin")
        assert row is not None and row.plugin_id == "doujin" and row.is_enabled == 1
        keys = session.scalars(
            select(TypeField.field_key).where(TypeField.data_type_id == "doujin")
        ).all()
        assert set(keys) == {"title", "engine"}
        orders = session.execute(
            select(SourceOrder.field_key, SourceOrder.source, SourceOrder.is_excluded)
            .where(SourceOrder.data_type_id == "doujin")
            .order_by(SourceOrder.field_key, SourceOrder.position)
        ).all()
    assert [tuple(row) for row in orders] == [
        ("", "alpha", 0),
        ("", "beta", 0),
        ("engine", "beta", 0),
        ("engine", "alpha", 1),
    ]
    assert "doujin" in [t["id"] for t in client.get("/api/data-types").json()]


def test_user_changed_orders_survive_reload(client: TestClient) -> None:
    factory = client.app.state.session_factory
    with factory() as session:
        row = session.get(SourceOrder, ("doujin", "", "alpha"))
        assert row is not None
        row.position = 9
        session.commit()
    assert client.post("/api/plugins/reload").status_code == 200
    with factory() as session:
        row = session.get(SourceOrder, ("doujin", "", "alpha"))
        assert row is not None and row.position == 9


def test_list_hides_secret_values(client: TestClient) -> None:
    response = client.put(
        "/api/plugins/alpha", json={"secrets": {"app_id": "id-1", "app_secret": "s3cret"}}
    )
    assert response.status_code == 200
    body = client.get("/api/plugins").json()
    alpha = next(p for p in body if p["id"] == "alpha")
    assert alpha["secrets"] == {"app_id": True, "app_secret": True, "access_token": False}
    assert alpha["oauth"] is True and alpha["logged_in"] is False
    assert alpha["notice"] == "没登录"
    assert alpha["settings"] == {"limit": 5}
    assert "s3cret" not in client.get("/api/plugins").text


def test_unknown_secret_key_rejected(client: TestClient) -> None:
    response = client.put("/api/plugins/alpha", json={"secrets": {"refresh_token": "x"}})
    assert response.status_code == 422


def test_settings_validated_and_saved(client: TestClient) -> None:
    assert client.put("/api/plugins/alpha", json={"settings": {"limit": "x"}}).status_code == 422
    response = client.put("/api/plugins/alpha", json={"settings": {"limit": 9}})
    assert response.json()["settings"] == {"limit": 9}


def test_disabling_data_type_plugin_keeps_rows(client: TestClient) -> None:
    response = client.put("/api/plugins/doujin", json={"enabled": False})
    assert response.status_code == 200 and response.json()["enabled"] is False
    with client.app.state.session_factory() as session:
        row = session.get(DataType, "doujin")
        assert row is not None and row.is_enabled == 0
    assert "doujin" not in [t["id"] for t in client.get("/api/data-types").json()]
    client.put("/api/plugins/doujin", json={"enabled": True})
    assert "doujin" in [t["id"] for t in client.get("/api/data-types").json()]


def test_reload_picks_up_code_changes(client: TestClient, folders: tuple[Path, Path]) -> None:
    builtin, _data = folders
    _write(builtin, "alpha", "source", SOURCE.replace('name = "Alpha"', 'name = "Alpha2"'))
    client.post("/api/plugins/reload")
    plugin = client.app.state.services.plugins.source("alpha")
    assert plugin is not None and plugin.name == "Alpha2"


def test_user_folder_overrides_builtin(client: TestClient, folders: tuple[Path, Path]) -> None:
    _builtin, data = folders
    _write(data / "plugins", "alpha", "source", SOURCE.replace('name = "Alpha"', 'name = "自己的"'))
    body = client.post("/api/plugins/reload").json()
    alpha = next(p for p in body if p["id"] == "alpha")
    assert alpha["origin"] == "user"


def test_bad_plugins_report_errors(tmp_path: Path) -> None:
    _write(tmp_path, "evil", "source", "import httpx2\nPLUGIN = None\n")
    _write(tmp_path, "inner", "source", "from mortis.db import models\nPLUGIN = None\n")
    _write(tmp_path, "boom", "source", "raise RuntimeError('坏了')\n")
    (tmp_path / "nomanifest").mkdir()
    _write(tmp_path, "movie2", "data_type", DATA_TYPE.replace('id="doujin"', 'id="movie"'))
    registry = PluginRegistry([(tmp_path, "builtin")])
    registry.load()
    errors = {info.id: info.error for info in registry.all()}
    assert "httpx2" in (errors["evil"] or "")
    assert "mortis.db" in (errors["inner"] or "")
    assert "坏了" in (errors["boom"] or "")
    assert "plugin.toml" in (errors["nomanifest"] or "")
    assert "内置类型重名" in (errors["movie2"] or "")
    assert registry.data_types() == []


def test_oauth_login_and_callback(client: TestClient, site: FakeSite) -> None:
    assert client.get("/api/plugins/alpha/oauth/login", follow_redirects=False).status_code == 422
    client.put("/api/plugins/alpha", json={"secrets": {"app_id": "id-1", "app_secret": "s3cret"}})
    login = client.get("/api/plugins/alpha/oauth/login", follow_redirects=False)
    assert login.status_code == 307
    target = urlsplit(login.headers["location"])
    query = parse_qs(target.query)
    assert target.netloc == "alpha.test"
    assert query["client_id"] == ["id-1"]
    redirect_uri = query["redirect_uri"][0]
    assert redirect_uri == f"{LOCAL}/api/plugins/alpha/oauth/callback"

    bad = client.get(
        "/api/plugins/alpha/oauth/callback",
        params={"code": "c", "state": "forged"},
        follow_redirects=False,
    )
    assert bad.status_code == 422

    site.token_responses.append(
        {"access_token": "tok-1", "refresh_token": "ref-1", "expires_in": 604800}
    )
    done = client.get(
        "/api/plugins/alpha/oauth/callback",
        params={"code": "the-code", "state": query["state"][0]},
        follow_redirects=False,
    )
    assert done.status_code == 307 and done.headers["location"].startswith("/settings")
    token_request = site.requests[-1]
    form = parse_qs(token_request.content.decode())
    assert form["grant_type"] == ["authorization_code"] and form["code"] == ["the-code"]
    assert form["redirect_uri"] == [redirect_uri]

    alpha = client.put("/api/plugins/alpha", json={}).json()
    assert alpha["logged_in"] is True and alpha["notice"] is None
    secrets = client.app.state.config_store.load_secrets().plugins["alpha"]
    assert secrets["access_token"] == "tok-1" and secrets["refresh_token"] == "ref-1"

    reused = client.get(
        "/api/plugins/alpha/oauth/callback",
        params={"code": "again", "state": query["state"][0]},
        follow_redirects=False,
    )
    assert reused.status_code == 422

    assert client.delete("/api/plugins/alpha/oauth").json()["logged_in"] is False


def test_token_refreshed_before_expiry(client: TestClient, site: FakeSite) -> None:
    services = client.app.state.services
    store = services.config_store
    store.update_plugin_secrets(
        "alpha",
        {
            "app_id": "id-1",
            "app_secret": "s3cret",
            "access_token": "old",
            "refresh_token": "ref-1",
            "expires_at": int(time.time()) + 3600,
            "redirect_uri": "http://127.0.0.1/cb",
        },
    )
    site.token_responses.append({"access_token": "new", "refresh_token": "ref-2", "expires_in": 9})
    plugin = services.plugins.source("alpha")
    logs: list[str] = []
    with services.http_client() as http:
        host = PluginHost(plugin, http, store, logs.append)
        assert host.access_token() == "new"
    form = parse_qs(site.requests[-1].content.decode())
    assert form["grant_type"] == ["refresh_token"] and form["refresh_token"] == ["ref-1"]
    assert store.load_secrets().plugins["alpha"]["refresh_token"] == "ref-2"
    assert not any("ref-1" in line or "new" in line.split() for line in logs)


def test_host_maps_auth_errors_and_logs_masked(client: TestClient) -> None:
    services = client.app.state.services
    plugin = services.plugins.source("alpha")
    logs: list[str] = []
    with services.http_client() as http:
        host = PluginHost(plugin, http, services.config_store, logs.append)
        assert host.get_json("https://alpha.test/item/1", params={"token": "abc"}) == {
            "name": "条目"
        }
        with pytest.raises(SourceAuthError):
            host.get_json("https://alpha.test/item/401")
    assert logs[0] == "[Alpha] GET https://alpha.test/item/1?token=%2A%2A%2A"
    assert "abc" not in "".join(logs)


def test_mask_url_keeps_plain_params() -> None:
    assert mask_url("https://x.test/a?q=1&api_key=k") == "https://x.test/a?q=1&api_key=%2A%2A%2A"
