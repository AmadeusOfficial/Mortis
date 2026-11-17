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

from mortis.plugin_api import Account, Hit, OAuth, SourcePlugin


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

    def account(self, host):
        token = host.access_token()
        if not token:
            return None
        host.get_json(f"https://alpha.test/me/{token}")
        return Account(name=f"{token} 的主人")

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
        if request.url.path in ("/item/401", "/me/bad"):
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
    assert "login=failed" in bad.headers["location"]

    site.token_responses.append(
        {"access_token": "tok-1", "refresh_token": "ref-1", "expires_in": 604800}
    )
    done = client.get(
        "/api/plugins/alpha/oauth/callback",
        params={"code": "the-code", "state": query["state"][0]},
        follow_redirects=False,
    )
    assert done.status_code == 307
    assert done.headers["location"] == "/settings?plugin=alpha&login=ok"
    token_request = site.requests[-1]
    form = parse_qs(token_request.content.decode())
    assert form["grant_type"] == ["authorization_code"] and form["code"] == ["the-code"]
    assert form["redirect_uri"] == [redirect_uri]

    alpha = client.put("/api/plugins/alpha", json={}).json()
    assert alpha["logged_in"] is True and alpha["notice"] is None
    secrets = client.app.state.config_store.load_secrets().sources["alpha"]
    assert secrets["access_token"] == "tok-1" and secrets["refresh_token"] == "ref-1"

    reused = client.get(
        "/api/plugins/alpha/oauth/callback",
        params={"code": "again", "state": query["state"][0]},
        follow_redirects=False,
    )
    assert "login=failed" in reused.headers["location"]

    assert client.get("/api/plugins/alpha/account").json() == {
        "account": {"name": "tok-1 的主人", "avatar_url": None, "url": None},
        "error": None,
    }
    assert client.delete("/api/plugins/alpha/oauth").json()["logged_in"] is False
    assert client.get("/api/plugins/alpha/account").json() == {"account": None, "error": None}


def test_oauth_denied_returns_to_settings(client: TestClient) -> None:
    client.put("/api/plugins/alpha", json={"secrets": {"app_id": "id-1", "app_secret": "s3cret"}})
    login = client.get("/api/plugins/alpha/oauth/login", follow_redirects=False)
    state = parse_qs(urlsplit(login.headers["location"]).query)["state"][0]
    denied = client.get(
        "/api/plugins/alpha/oauth/callback",
        params={"error": "access_denied", "state": state},
        follow_redirects=False,
    )
    target = urlsplit(denied.headers["location"])
    assert target.path == "/settings"
    assert parse_qs(target.query) == {
        "plugin": ["alpha"],
        "login": ["failed"],
        "reason": ["在授权页取消了授权"],
    }


def test_pasted_token_drops_refresh(client: TestClient) -> None:
    store = client.app.state.config_store
    store.update_plugin_secrets(
        "alpha", {"access_token": "old", "refresh_token": "ref-1", "expires_at": 99}
    )
    alpha = client.put("/api/plugins/alpha", json={"secrets": {"access_token": " mine "}}).json()
    assert alpha["logged_in"] is True and alpha["expires_at"] is None
    saved = store.load_secrets().sources["alpha"]
    assert saved["access_token"] == "mine" and not saved.get("refresh_token")


def test_account_reports_rejected_token(client: TestClient) -> None:
    client.app.state.config_store.update_plugin_secrets("alpha", {"access_token": "bad"})
    assert client.get("/api/plugins/alpha/account").json() == {
        "account": None,
        "error": "令牌已失效，请重新登录",
    }


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
    assert store.load_secrets().sources["alpha"]["refresh_token"] == "ref-2"
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


def test_source_orders_read_write_reset(client: TestClient) -> None:
    body = client.get("/api/data-types/doujin/source-orders").json()
    assert [s["id"] for s in body["sources"]] == ["alpha"]
    assert body["whole"] == [
        {"source": "alpha", "excluded": False},
        {"source": "beta", "excluded": False},
    ]
    fields = {f["key"]: f for f in body["fields"]}
    assert fields["title"]["custom"] is False
    assert fields["engine"]["custom"] is True
    assert fields["engine"]["order"][1] == {"source": "alpha", "excluded": True}

    changed = client.put(
        "/api/data-types/doujin/source-orders",
        json={
            "orders": {
                "": [{"source": "beta"}, {"source": "alpha"}],
                "engine": None,
                "title": [{"source": "alpha"}, {"source": "beta", "excluded": True}],
            }
        },
    ).json()
    fields = {f["key"]: f for f in changed["fields"]}
    assert [e["source"] for e in changed["whole"]] == ["beta", "alpha"]
    assert fields["engine"]["custom"] is False
    assert fields["title"]["order"][1]["excluded"] is True

    bad = client.put("/api/data-types/doujin/source-orders", json={"orders": {"nope": None}})
    assert bad.status_code == 422
    assert client.get("/api/data-types/nope/source-orders").status_code == 404

    reset = client.post("/api/data-types/doujin/source-orders/reset").json()
    fields = {f["key"]: f for f in reset["fields"]}
    assert [e["source"] for e in reset["whole"]] == ["alpha", "beta"]
    assert fields["engine"]["custom"] is True and fields["title"]["custom"] is False


BUNDLED_IDS = ("bangumi", "vndb", "ymgal", "cngal")


@pytest.fixture
def bundled(tmp_path: Path):
    """用仓库里自带的 galgame 插件目录（含子来源）加载一个注册表。"""
    repo = Path(__file__).resolve().parents[1]
    data = tmp_path / "data"
    registry = PluginRegistry([(repo / "plugins", "builtin"), (data / "plugins", "user")])
    return registry, data


def test_bundled_sources_load_with_data_type(bundled) -> None:
    registry, _data = bundled
    registry.load()
    sources = registry.sources("galgame")
    assert {source.id for source in sources} == set(BUNDLED_IDS)
    infos = {info.id: info for info in registry.all()}
    for source_id in BUNDLED_IDS:
        info = infos[source_id]
        assert info.kind == "source"
        assert info.parent == "galgame"
        assert info.active
    _assert_unique_ids(registry)


def test_disabling_data_type_disables_its_sources(bundled) -> None:
    registry, _data = bundled
    registry.load(disabled=["galgame"])
    assert registry.sources("galgame") == []
    infos = {info.id: info for info in registry.all()}
    for source_id in BUNDLED_IDS:
        info = infos[source_id]
        assert info.kind == "source"
        assert info.parent == "galgame"
        assert info.enabled is False
        assert not info.active


def test_disabling_one_bundled_source(bundled) -> None:
    registry, _data = bundled
    registry.load(disabled=["cngal"])
    active = {source.id for source in registry.sources("galgame")}
    assert active == {"bangumi", "vndb", "ymgal"}
    cngal = registry.get("cngal")
    assert cngal is not None and cngal.parent == "galgame" and not cngal.active


BAD_TYPE = """
from mortis.plugin_api import DataTypePlugin, DataTypeSpec, FieldSpec, SourcePlugin


class Beta(SourcePlugin):
    id = "beta"
    name = "Beta"
    data_type = "zzz"
    provider = "beta"

    def search(self, host, title):
        return []

    def fetch(self, host, external_id):
        return {}

    def candidates(self, raw):
        return {}


PLUGIN = DataTypePlugin(
    spec=DataTypeSpec(
        id="doujin",
        name="同人游戏",
        structure="item",
        fields=[
            FieldSpec(field_key="title", label="名称", value_type="text",
                      stored_in="items_column", column_name="title", detail_section="基本信息"),
        ],
    ),
    sources=(Beta(),),
)
"""

DUP_TYPE = """
from mortis.plugin_api import DataTypePlugin, DataTypeSpec, FieldSpec, SourcePlugin


class One(SourcePlugin):
    id = "dup"
    name = "One"
    data_type = "doujin"
    provider = "dup"

    def search(self, host, title):
        return []

    def fetch(self, host, external_id):
        return {}

    def candidates(self, raw):
        return {}


class Two(SourcePlugin):
    id = "dup"
    name = "Two"
    data_type = "doujin"
    provider = "dup"

    def search(self, host, title):
        return []

    def fetch(self, host, external_id):
        return {}

    def candidates(self, raw):
        return {}


PLUGIN = DataTypePlugin(
    spec=DataTypeSpec(
        id="doujin",
        name="同人游戏",
        structure="item",
        fields=[
            FieldSpec(field_key="title", label="名称", value_type="text",
                      stored_in="items_column", column_name="title", detail_section="基本信息"),
        ],
    ),
    sources=(One(), Two()),
)
"""


def test_bundled_source_must_match_type(tmp_path: Path) -> None:
    _write(tmp_path, "doujin", "data_type", BAD_TYPE)
    registry = PluginRegistry([(tmp_path, "builtin")])
    registry.load()
    info = registry.get("doujin")
    assert info is not None and info.error is not None
    assert "doujin" in info.error and "zzz" in info.error


def test_bundled_source_id_must_be_unique(tmp_path: Path) -> None:
    _write(tmp_path, "doujin", "data_type", DUP_TYPE)
    registry = PluginRegistry([(tmp_path, "builtin")])
    registry.load()
    info = registry.get("doujin")
    assert info is not None and info.error is not None
    assert "dup" in info.error


def test_bundled_source_settings_and_oauth_keep_ids(bundled) -> None:
    registry, data = bundled
    app = create_app(
        Settings(data_dir=data, plugins_dir=Path(__file__).resolve().parents[1] / "plugins"),
        start_worker=False,
    )
    with TestClient(app, base_url=LOCAL) as client:
        settings = client.put(
            "/api/plugins/ymgal", json={"settings": {"client_id": "me", "client_secret": "s"}}
        )
        assert settings.status_code == 200
        raw = client.app.state.config_store.load_config().sources["ymgal"]
        assert raw["client_id"] == "me"

        body = client.get("/api/plugins").json()
        for source_id in BUNDLED_IDS:
            entry = next(p for p in body if p["id"] == source_id)
            assert entry["parent"] == "galgame"
        client.put("/api/plugins/bangumi", json={"secrets": {"app_id": "a", "app_secret": "b"}})
        login = client.get("/api/plugins/bangumi/oauth/login", follow_redirects=False)
        assert login.status_code == 307
        target = urlsplit(login.headers["location"])
        redirect_uri = parse_qs(target.query)["redirect_uri"][0]
        assert redirect_uri == f"{LOCAL}/api/plugins/bangumi/oauth/callback"


def _write_type_with_sources(
    folder: Path,
    type_id: str,
    source_ids: list[str],
    *,
    toml_sources: list[tuple[str, str]] | None = None,
    data_type_of: dict[str, str] | None = None,
) -> None:
    """写一个带自带来源的数据类型插件；toml_sources 不给时按 source_ids 自动生成。"""
    data_type_of = data_type_of or {}
    classes = []
    instances = []
    for index, source_id in enumerate(source_ids):
        class_name = f"S{index}"
        owner = data_type_of.get(source_id, type_id)
        classes.append(
            f'''
class {class_name}(SourcePlugin):
    id = "{source_id}"
    name = "{source_id}"
    data_type = "{owner}"
    provider = "{source_id}"

    def search(self, host, title):
        return []

    def fetch(self, host, external_id):
        return {{}}

    def candidates(self, raw):
        return {{}}
'''
        )
        instances.append(f"{class_name}()")
    code = (
        "from mortis.plugin_api import DataTypePlugin, DataTypeSpec, FieldSpec, SourcePlugin\n"
        + "".join(classes)
        + f'''
PLUGIN = DataTypePlugin(
    spec=DataTypeSpec(
        id="{type_id}",
        name="{type_id}",
        structure="item",
        fields=[
            FieldSpec(field_key="title", label="名称", value_type="text",
                      stored_in="items_column", column_name="title", detail_section="基本信息"),
        ],
    ),
    sources=({", ".join(instances)}{"," if len(instances) == 1 else ""}),
)
'''
    )
    target = folder / type_id
    target.mkdir(parents=True, exist_ok=True)
    listed = toml_sources if toml_sources is not None else [(sid, sid) for sid in source_ids]
    toml = f'id = "{type_id}"\nname = "{type_id}"\nversion = "1.0"\nkind = "data_type"\n'
    for source_id, name in listed:
        toml += f'\n[[sources]]\nid = "{source_id}"\nname = "{name}"\n'
    (target / "plugin.toml").write_text(toml, encoding="utf-8")
    (target / "__init__.py").write_text(code, encoding="utf-8")


def test_toml_sources_must_match_object_sources(tmp_path: Path) -> None:
    # 对象里有 beta，但 plugin.toml 的 [[sources]] 名单少了 beta。
    _write_type_with_sources(tmp_path, "doujin", ["beta"], toml_sources=[])
    registry = PluginRegistry([(tmp_path, "builtin")])
    registry.load()
    info = registry.get("doujin")
    assert info is not None and info.error is not None
    assert "不一致" in info.error and "beta" in info.error


def test_toml_sources_extra_id_reported(tmp_path: Path) -> None:
    # plugin.toml 多列了 gamma，但对象里没有。
    _write_type_with_sources(
        tmp_path, "doujin", ["beta"], toml_sources=[("beta", "Beta"), ("gamma", "Gamma")]
    )
    registry = PluginRegistry([(tmp_path, "builtin")])
    registry.load()
    info = registry.get("doujin")
    assert info is not None and info.error is not None
    assert "不一致" in info.error and "gamma" in info.error


def test_toml_source_name_required(tmp_path: Path) -> None:
    _write_type_with_sources(tmp_path, "doujin", ["beta"], toml_sources=[("beta", "   ")])
    registry = PluginRegistry([(tmp_path, "builtin")])
    registry.load()
    info = registry.get("doujin")
    assert info is not None and info.error is not None
    assert "name" in info.error


def test_disabled_parent_uses_toml_source_name(bundled) -> None:
    registry, _data = bundled
    registry.load(disabled=["galgame"])
    ymgal = registry.get("ymgal")
    assert ymgal is not None
    assert ymgal.name == "月幕 Galgame"
    assert ymgal.parent == "galgame" and not ymgal.active


def test_failed_parent_disables_sources_with_error(tmp_path: Path) -> None:
    target = tmp_path / "doujin"
    target.mkdir(parents=True)
    (target / "plugin.toml").write_text(
        'id = "doujin"\nname = "doujin"\nversion = "1.0"\nkind = "data_type"\n'
        '\n[[sources]]\nid = "beta"\nname = "Beta"\n',
        encoding="utf-8",
    )
    (target / "__init__.py").write_text("raise RuntimeError('坏了')\n", encoding="utf-8")
    registry = PluginRegistry([(tmp_path, "builtin")])
    registry.load()
    parent = registry.get("doujin")
    assert parent is not None and parent.error is not None and "坏了" in parent.error
    beta = registry.get("beta")
    assert beta is not None
    assert beta.enabled is False and not beta.active
    assert beta.parent == "doujin"
    assert beta.error is not None and "doujin" in beta.error
    assert beta.name == "Beta"


def test_user_data_type_overrides_builtin_without_leftover_sources(tmp_path: Path) -> None:
    builtin = tmp_path / "builtin"
    data = tmp_path / "data"
    _write_type_with_sources(builtin, "doujin", ["alpha", "beta"])
    _write_type_with_sources(data / "plugins", "doujin", ["gamma"])
    registry = PluginRegistry([(builtin, "builtin"), (data / "plugins", "user")])
    registry.load()
    assert {source.id for source in registry.sources("doujin")} == {"gamma"}
    assert registry.get("alpha") is None
    assert registry.get("beta") is None
    gamma = registry.get("gamma")
    assert gamma is not None and gamma.origin == "user" and gamma.active


STANDALONE_BANGUMI = """
from mortis.plugin_api import Hit, SourcePlugin


class Standalone(SourcePlugin):
    id = "bangumi"
    name = "我的 Bangumi"
    data_type = "other"
    provider = "bangumi"

    def search(self, host, title):
        return [Hit(external_id="1", title=title)]

    def fetch(self, host, external_id):
        return {}

    def candidates(self, raw):
        return {}


PLUGIN = Standalone()
"""


def _assert_unique_ids(registry: PluginRegistry) -> None:
    ids = [info.id for info in registry.all()]
    assert len(ids) == len(set(ids)), f"列表里有重复 id：{ids}"


def test_standalone_source_clashing_bundled_id_dropped(tmp_path: Path) -> None:
    builtin = tmp_path / "builtin"
    data = tmp_path / "data"
    _write_type_with_sources(builtin, "doujin", ["bangumi"], data_type_of={"bangumi": "doujin"})
    _write(data / "plugins", "bangumi", "source", STANDALONE_BANGUMI)
    registry = PluginRegistry([(builtin, "builtin"), (data / "plugins", "user")])
    registry.load()
    # primary id 已被内置子来源占用：用户独立 bangumi 不列出，内置子来源仍可用，id 唯一。
    active = registry.source("bangumi")
    assert active is not None and active.data_type == "doujin"
    bangumi = registry.get("bangumi")
    assert bangumi is not None and bangumi.parent == "doujin" and bangumi.error is None
    _assert_unique_ids(registry)


def test_two_data_types_sharing_source_id_errors(tmp_path: Path) -> None:
    builtin = tmp_path / "builtin"
    _write_type_with_sources(builtin, "doujin", ["shared"])
    _write_type_with_sources(builtin, "eroge", ["shared"], data_type_of={"shared": "eroge"})
    registry = PluginRegistry([(builtin, "builtin")])
    registry.load()
    # 先到的 doujin 保留其子来源，后到的 eroge（primary id 还空着）列成一条错误条目，不列子来源。
    assert registry.source("shared") is not None
    eroge = registry.get("eroge")
    assert eroge is not None and eroge.error is not None
    assert "shared" in eroge.error and "重名" in eroge.error
    _assert_unique_ids(registry)


def test_user_override_conflict_keeps_builtin_and_unique_ids(tmp_path: Path) -> None:
    builtin = tmp_path / "builtin"
    data = tmp_path / "data"
    # 内置：doujin 带 bangumi 子来源；另一个内置类型 eroge 带 shared 子来源。
    _write_type_with_sources(builtin, "doujin", ["bangumi"], data_type_of={"bangumi": "doujin"})
    _write_type_with_sources(builtin, "eroge", ["shared"], data_type_of={"shared": "eroge"})
    # 用户想覆盖 doujin，但它的子来源 shared 和内置 eroge 的 shared 冲突。
    _write_type_with_sources(
        data / "plugins",
        "doujin",
        ["bangumi", "shared"],
        data_type_of={"bangumi": "doujin", "shared": "doujin"},
    )
    registry = PluginRegistry([(builtin, "builtin"), (data / "plugins", "user")])
    registry.load()
    # 冲突：用户覆盖包被跳过，内置 doujin 原样可用（origin 仍是 builtin），列表 id 唯一。
    doujin = registry.get("doujin")
    assert doujin is not None and doujin.origin == "builtin" and doujin.active
    assert {source.id for source in registry.sources("doujin")} == {"bangumi"}
    assert registry.source("shared") is not None
    _assert_unique_ids(registry)


GALTYPE_INIT = """
from mortis.plugin_api import DataTypePlugin, DataTypeSpec, FieldSpec, SourcePlugin


class Sub1(SourcePlugin):
    id = "sub1"
    name = "Sub1"
    data_type = "galtype"
    provider = "sub1"

    def search(self, host, title):
        return []

    def fetch(self, host, external_id):
        return {}

    def candidates(self, raw):
        # 延迟相对导入：读本包的 marker，和复验者的 lazy_import_value 写法一致。
        from .marker import VALUE

        return {"version": VALUE}


PLUGIN = DataTypePlugin(
    spec=DataTypeSpec(
        id="galtype",
        name="galtype",
        structure="item",
        fields=[
            FieldSpec(field_key="title", label="名称", value_type="text",
                      stored_in="items_column", column_name="title", detail_section="基本信息"),
        ],
    ),
    sources=(Sub1(),),
)
"""


def _write_galtype(folder: Path, marker: str, source_ids: list[str]) -> None:
    target = folder / "galtype"
    target.mkdir(parents=True, exist_ok=True)
    toml = 'id = "galtype"\nname = "galtype"\nversion = "1.0"\nkind = "data_type"\n'
    for source_id in source_ids:
        toml += f'\n[[sources]]\nid = "{source_id}"\nname = "{source_id}"\n'
    (target / "plugin.toml").write_text(toml, encoding="utf-8")
    init = GALTYPE_INIT
    # 覆盖包额外声明 clashme 子来源，用来和内置 other 冲突。
    if "clashme" in source_ids:
        init = init.replace("sources=(Sub1(),)", "sources=(Sub1(), Clashme())").replace(
            "PLUGIN = DataTypePlugin(",
            "class Clashme(SourcePlugin):\n"
            '    id = "clashme"\n'
            '    name = "Clashme"\n'
            '    data_type = "galtype"\n'
            '    provider = "clashme"\n'
            "    def search(self, host, title):\n        return []\n"
            "    def fetch(self, host, external_id):\n        return {}\n"
            "    def candidates(self, raw):\n        return {}\n\n\n"
            "PLUGIN = DataTypePlugin(",
        )
    (target / "__init__.py").write_text(init, encoding="utf-8")
    (target / "marker.py").write_text(f'VALUE = "{marker}"\n', encoding="utf-8")


def test_rejected_override_does_not_poison_sys_modules(tmp_path: Path) -> None:
    import sys

    builtin = tmp_path / "builtin"
    data = tmp_path / "data"
    # 内置 galtype（marker=old，带 sub1）与内置独立来源 clashme。
    _write_galtype(builtin, "old", ["sub1"])
    _write(
        builtin,
        "clashme",
        "source",
        STANDALONE_BANGUMI.replace('id = "bangumi"', 'id = "clashme"').replace(
            'provider = "bangumi"', 'provider = "clashme"'
        ),
    )
    # user 想覆盖 galtype（marker=new），但新增的 clashme 子来源和内置 clashme 冲突。
    _write_galtype(data / "plugins", "new", ["sub1", "clashme"])

    module_name = "mortis_plugins.galtype"
    sys.modules.pop(module_name, None)
    for key in [k for k in sys.modules if k.startswith(module_name + ".")]:
        sys.modules.pop(key, None)

    registry = PluginRegistry([(builtin, "builtin"), (data / "plugins", "user")])
    registry.load()

    # 内置 galtype 原样保留、可用，独立 clashme 可用，id 唯一。
    galtype = registry.get("galtype")
    assert galtype is not None and galtype.origin == "builtin" and galtype.active
    assert registry.source("clashme") is not None
    _assert_unique_ids(registry)

    # 被拒的覆盖包没有写进 sys.modules：内置 galtype 模块仍在。
    assert sys.modules.get(module_name) is not None
    assert str(sys.modules[module_name].__file__).startswith(str(builtin))
    # 延迟相对导入：内置 sub1 现在才读 marker，应拿到 old 代码而不是被拒覆盖包的 new。
    sub1 = registry.source("sub1")
    assert sub1 is not None
    assert sub1.candidates({}) == {"version": "old"}


def test_source_cannot_declare_sources(tmp_path: Path) -> None:
    target = tmp_path / "lone"
    target.mkdir(parents=True)
    (target / "plugin.toml").write_text(
        'id = "lone"\nname = "lone"\nversion = "1.0"\nkind = "source"\n'
        '\n[[sources]]\nid = "sub"\nname = "Sub"\n',
        encoding="utf-8",
    )
    (target / "__init__.py").write_text("PLUGIN = None\n", encoding="utf-8")
    registry = PluginRegistry([(tmp_path, "builtin")])
    registry.load()
    info = registry.get("lone")
    assert info is not None and info.error is not None
    assert "[[sources]]" in info.error


def test_player_cannot_declare_sources(tmp_path: Path) -> None:
    target = tmp_path / "myplayer"
    target.mkdir(parents=True)
    (target / "plugin.toml").write_text(
        'id = "myplayer"\nname = "myplayer"\nversion = "1.0"\nkind = "player"\n'
        '\n[[sources]]\nid = "sub"\nname = "Sub"\n',
        encoding="utf-8",
    )
    (target / "__init__.py").write_text("PLUGIN = None\n", encoding="utf-8")
    registry = PluginRegistry([(tmp_path, "builtin")])
    registry.load()
    info = registry.get("myplayer")
    assert info is not None and info.error is not None
    assert "[[sources]]" in info.error


def test_mask_url_keeps_plain_params() -> None:
    assert mask_url("https://x.test/a?q=1&api_key=k") == "https://x.test/a?q=1&api_key=%2A%2A%2A"
    assert mask_url("https://x.test/a?keyword=dies&accessToken=t") == (
        "https://x.test/a?keyword=dies&accessToken=%2A%2A%2A"
    )
