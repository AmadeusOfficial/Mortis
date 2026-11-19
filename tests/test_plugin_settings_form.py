"""插件设置表单：schema 规范化，列表和嵌套对象经设置页保存、读回。"""

import textwrap
import tomllib
from pathlib import Path
from typing import Any, Literal

from fastapi.testclient import TestClient
from pydantic import BaseModel, Field

from mortis.api.app import create_app
from mortis.plugins.settings_form import form_schema, storable
from mortis.settings import Settings


class Rule(BaseModel):
    pattern: str
    tag: str
    enabled: bool = True


class FormSettings(BaseModel):
    words: list[str] = []
    rules: list[Rule] = []
    mode: Literal["a", "b"] = "a"
    limit: int | None = None
    rule: Rule | None = None
    level: int = Field(default=5, ge=1, le=10)


def _walk(node: Any) -> list[Any]:
    found = [node]
    if isinstance(node, dict):
        for value in node.values():
            found.extend(_walk(value))
    elif isinstance(node, list):
        for value in node:
            found.extend(_walk(value))
    return found


def _refs(schema: dict[str, Any]) -> list[str]:
    return [n["$ref"] for n in _walk(schema) if isinstance(n, dict) and "$ref" in n]


def test_form_schema_drops_null_branches_and_keeps_refs() -> None:
    schema = form_schema(FormSettings)
    assert {"type": "null"} not in _walk(schema)
    assert not any(isinstance(n, dict) and ("anyOf" in n or "oneOf" in n) for n in _walk(schema))
    props = schema["properties"]
    assert props["limit"]["type"] == "integer" and "default" not in props["limit"]
    assert props["limit"]["title"] == "Limit"
    assert props["words"] == {
        "items": {"type": "string"},
        "type": "array",
        "default": [],
        "title": "Words",
    }
    assert props["mode"]["enum"] == ["a", "b"] and props["mode"]["default"] == "a"
    assert (props["level"]["minimum"], props["level"]["maximum"]) == (1, 10)
    refs = _refs(schema)
    assert "#/$defs/Rule" in refs and props["rule"]["$ref"] == "#/$defs/Rule"
    for ref in refs:
        assert ref.removeprefix("#/$defs/") in schema["$defs"]
    # 原模型的 schema 不被改动。
    assert "anyOf" in FormSettings.model_json_schema()["properties"]["limit"]


def test_form_schema_normalizes_inside_defs() -> None:
    class Inner(BaseModel):
        note: str | None = None

    class Outer(BaseModel):
        inner: list[Inner] = []

    schema = form_schema(Outer)
    assert schema["$defs"]["Inner"]["properties"]["note"] == {"type": "string", "title": "Note"}


def test_storable_drops_none_recursively() -> None:
    values = {"a": None, "b": [{"x": None, "y": 1}, 2], "c": {"d": None, "e": "f"}}
    assert storable(values) == {"b": [{"y": 1}, 2], "c": {"e": "f"}}


SOURCE = """
from typing import Literal

from pydantic import BaseModel, Field

from mortis.plugin_api import Hit, Plugin, SourcePlugin


class Rule(BaseModel):
    pattern: str
    tag: str
    enabled: bool = True


class FormSettings(BaseModel):
    words: list[str] = []
    rules: list[Rule] = []
    mode: Literal["a", "b"] = "a"
    limit: int | None = None
    rule: Rule | None = None
    level: int = Field(default=5, ge=1, le=10)


class Src(SourcePlugin):
    id = "me.form"
    name = "表单"
    data_type = "movie"
    provider = "me"
    Settings = FormSettings

    def search(self, host, title):
        return [Hit(external_id="1", title=title)]

    def fetch(self, host, external_id):
        return {}

    def candidates(self, raw):
        return {}


PLUGIN = Plugin(sources=(Src(),))
"""

TOML = """
id = "me.formpkg"
name = "表单包"
version = "1.0"

[mortis]
api_version = 1

[[sources]]
id = "me.form"
name = "表单"
data_type = "movie"
"""


def _app(tmp_path: Path) -> Any:
    builtin = tmp_path / "builtin"
    target = builtin / "form"
    target.mkdir(parents=True)
    (target / "plugin.toml").write_text(textwrap.dedent(TOML), encoding="utf-8")
    (target / "__init__.py").write_text(SOURCE, encoding="utf-8")
    return create_app(Settings(data_dir=tmp_path / "data", plugins_dir=builtin), start_worker=False)


def _listed(client: TestClient) -> dict[str, Any]:
    return {item["id"]: item for item in client.get("/api/plugins").json()}["me.form"]


def test_api_saves_lists_and_nested_objects(tmp_path: Path) -> None:
    values = {
        "words": ["甲", "乙"],
        "rules": [{"pattern": "a.*", "tag": "A"}, {"pattern": "b", "tag": "B", "enabled": False}],
        "mode": "b",
        "limit": 3,
        "rule": {"pattern": "c", "tag": "C"},
        "level": 7,
    }
    with TestClient(_app(tmp_path), base_url="http://127.0.0.1") as client:
        listed = _listed(client)
        assert {"type": "null"} not in _walk(listed["settings_schema"])
        assert listed["settings"]["limit"] is None
        saved = client.put("/api/plugins/me.form", json={"settings": values})
        assert saved.status_code == 200, saved.text
        expected = {
            **values,
            "rules": [{**values["rules"][0], "enabled": True}, values["rules"][1]],
            "rule": {"pattern": "c", "tag": "C", "enabled": True},
        }
        assert saved.json()["settings"] == expected
        assert _listed(client)["settings"] == expected
    raw = tomllib.loads((tmp_path / "data" / "config.toml").read_text(encoding="utf-8"))
    assert raw["sources"]["me.form"] == expected


def test_api_saves_none_and_fills_defaults_on_read(tmp_path: Path) -> None:
    with TestClient(_app(tmp_path), base_url="http://127.0.0.1") as client:
        saved = client.put(
            "/api/plugins/me.form",
            json={
                "settings": {"limit": None, "rule": None, "rules": [{"pattern": "x", "tag": "X"}]}
            },
        )
        assert saved.status_code == 200, saved.text
        settings = _listed(client)["settings"]
        assert settings["limit"] is None and settings["rule"] is None
        assert settings["rules"] == [{"pattern": "x", "tag": "X", "enabled": True}]
        assert settings["level"] == 5
    raw = tomllib.loads((tmp_path / "data" / "config.toml").read_text(encoding="utf-8"))
    assert "limit" not in raw["sources"]["me.form"] and "rule" not in raw["sources"]["me.form"]


def test_api_rejects_bad_list_item_with_location(tmp_path: Path) -> None:
    with TestClient(_app(tmp_path), base_url="http://127.0.0.1") as client:
        bad_type = client.put(
            "/api/plugins/me.form",
            json={
                "settings": {
                    "rules": [{"pattern": "a", "tag": "A"}, {"pattern": {"x": 1}, "tag": "B"}]
                }
            },
        )
        assert bad_type.status_code == 422
        assert "rules.1.pattern" in bad_type.text
        missing = client.put(
            "/api/plugins/me.form", json={"settings": {"rules": [{"pattern": "a"}]}}
        )
        assert missing.status_code == 422
        assert "rules.0.tag" in missing.text
        out_of_range = client.put("/api/plugins/me.form", json={"settings": {"level": 11}})
        assert out_of_range.status_code == 422 and "level" in out_of_range.text
        assert _listed(client)["settings"]["rules"] == []
