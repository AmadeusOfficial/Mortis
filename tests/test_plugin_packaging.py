"""插件 zip 导入和卸载：静态检查、先预览后执行、令牌、覆盖回滚、卸载。"""

import io
import stat
import sys
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mortis.api.app import create_app
from mortis.errors import UserError
from mortis.plugins import packaging
from mortis.plugins.packaging import Limits, PluginInstaller, Preview
from mortis.plugins.registry import PluginRegistry
from mortis.services import KeyedLocks
from mortis.settings import Settings

CODE = """
from pathlib import Path
from mortis.plugin_api import Hit, SourcePlugin

Path({marker!r}).write_text("imported", encoding="utf-8")


class Src(SourcePlugin):
    id = "{pid}"
    name = "来源"
    data_type = "movie"
    provider = "me"

    def search(self, host, title):
        return [Hit(external_id="1", title=title)]

    def fetch(self, host, external_id):
        return {{}}

    def candidates(self, raw):
        return {{}}


PLUGIN = Src()
"""

TOML = """
id = "{pid}"
name = "示例"
version = "{version}"

[mortis]
api_version = 1
"""


_DEFAULT_MARKER: dict[str, str] = {}


@pytest.fixture(autouse=True)
def marker_in_tmp(tmp_path: Path) -> str:
    """测试插件被执行时写的标记文件：默认放进 tmp_path，不往当前工作目录写东西。"""
    path = str(tmp_path / "plugin-imported.txt")
    _DEFAULT_MARKER["path"] = path
    return path


def default_marker() -> str:
    assert _DEFAULT_MARKER, "要用 marker_in_tmp 这个自动夹具，不然测试插件会往工作目录写文件"
    return _DEFAULT_MARKER["path"]


def plugin_files(
    pid: str = "me.hello", version: str = "1.0", marker: str = "", toml: str | None = None
) -> dict[str, bytes]:
    return {
        "plugin.toml": (
            toml if toml is not None else TOML.format(pid=pid, version=version)
        ).encode(),
        "__init__.py": CODE.format(pid=pid, marker=marker or default_marker()).encode(),
    }


def make_zip(files: dict[str, bytes], prefix: str = "") -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(prefix + name, data)
    return buffer.getvalue()


def raw_zip(entries: list[tuple[str, bytes, int]]) -> bytes:
    """entries: (名字, 内容, external_attr)。名字原样写进去，不做规范化。"""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data, attr in entries:
            info = zipfile.ZipInfo(name)
            info.external_attr = attr
            archive.writestr(info, data)
    return buffer.getvalue()


class Env:
    def __init__(self, root: Path, limits: Limits | None = None) -> None:
        self.data = root / "data"
        self.user_dir = self.data / "plugins"
        self.builtin = root / "builtin"
        self.clock_now = 0.0
        builtin = self.builtin / "builtin_demo"
        builtin.mkdir(parents=True, exist_ok=True)
        (builtin / "plugin.toml").write_text(
            TOML.format(pid="builtin_demo", version="1.0"), encoding="utf-8"
        )
        (builtin / "__init__.py").write_text(
            CODE.format(pid="builtin_demo", marker=default_marker()), encoding="utf-8"
        )
        self.registry = PluginRegistry([(self.builtin, "builtin"), (self.user_dir, "user")])
        self.registry.load()
        self.installer = PluginInstaller(
            self.user_dir,
            self.registry,
            KeyedLocks(),
            lambda: self.registry.load(),
            limits=limits,
            clock=lambda: self.clock_now,
        )

    def check(self, data: bytes) -> Preview:
        return self.installer.inspect([data[i : i + 1000] for i in range(0, len(data), 1000)])


@pytest.fixture
def env(tmp_path: Path) -> Env:
    return Env(tmp_path)


def rejects(env: Env, data: bytes, text: str) -> None:
    with pytest.raises(UserError) as caught:
        env.check(data)
    assert text in caught.value.message
    assert not list(env.installer._tmp.glob("stage-*")) if env.installer._tmp.exists() else True


def test_install_fresh_hot_loads_after_confirm(env: Env, tmp_path: Path) -> None:
    marker = tmp_path / "marker.txt"
    preview = env.check(make_zip(plugin_files(marker=str(marker))))
    assert not marker.exists(), "预览阶段不能执行插件代码"
    assert preview.id == "me.hello" and preview.existing is None and not preview.needs_restart
    assert preview.capabilities == ("source",)
    assert {f.path for f in preview.files} == {"plugin.toml", "__init__.py"}
    assert all(len(f.sha256) == 64 for f in preview.files) and len(preview.package_hash) == 64
    assert env.registry.get("me.hello") is None
    result = env.installer.confirm(preview.token)
    assert marker.exists()
    assert not result.replaced and not result.needs_restart and result.previous_version is None
    info = env.registry.get("me.hello")
    assert info is not None and info.active and info.origin == "user"
    assert (env.user_dir / "me.hello" / "plugin.toml").is_file()


def test_single_top_folder_is_accepted(env: Env) -> None:
    preview = env.check(make_zip(plugin_files(), prefix="my_plugin/"))
    assert {f.path for f in preview.files} == {"plugin.toml", "__init__.py"}


def test_no_root_manifest_rejected(env: Env) -> None:
    rejects(env, make_zip({"a/plugin.toml": b"x", "b/__init__.py": b""}), "根目录")
    rejects(env, make_zip({"__init__.py": b""}), "根目录")


def test_missing_mortis_table_is_not_a_mortis_package(env: Env) -> None:
    toml = 'id = "me.hello"\nname = "x"\nversion = "1"\n'
    rejects(env, make_zip(plugin_files(toml=toml)), "这不是莫提斯插件包")


def test_token_cannot_be_forged_and_is_one_time(env: Env) -> None:
    preview = env.check(make_zip(plugin_files()))
    with pytest.raises(UserError) as caught:
        env.installer.confirm("nope")
    assert caught.value.status_code == 410
    env.installer.confirm(preview.token)
    with pytest.raises(UserError):
        env.installer.confirm(preview.token)


def test_failed_confirm_still_burns_token(env: Env) -> None:
    bad = plugin_files()
    bad["__init__.py"] = b"import subprocess\n"
    preview = env.check(make_zip(bad))
    with pytest.raises(UserError, match="试运行没通过"):
        env.installer.confirm(preview.token)
    with pytest.raises(UserError):
        env.installer.confirm(preview.token)
    assert env.registry.get("me.hello") is None
    assert not (env.user_dir / "me.hello").exists()


def test_token_expires_and_staging_is_cleaned(env: Env) -> None:
    preview = env.check(make_zip(plugin_files()))
    assert list(env.installer._tmp.glob("stage-*"))
    env.clock_now = 10_000
    with pytest.raises(UserError) as caught:
        env.installer.confirm(preview.token)
    assert caught.value.status_code == 410
    assert not list(env.installer._tmp.glob("stage-*"))


def test_pending_tokens_are_capped(env: Env) -> None:
    previews = [env.check(make_zip(plugin_files(f"me.p{i}"))) for i in range(10)]
    assert len(list(env.installer._tmp.glob("stage-*"))) <= env.installer.limits.max_pending
    with pytest.raises(UserError):
        env.installer.confirm(previews[0].token)
    env.installer.confirm(previews[-1].token)


def test_tmp_dir_cleared_on_startup(tmp_path: Path) -> None:
    first = Env(tmp_path)
    first.check(make_zip(plugin_files()))
    assert list(first.installer._tmp.glob("stage-*"))
    second = Env(tmp_path)
    assert not list(second.installer._tmp.glob("stage-*"))


@pytest.mark.parametrize(
    "name",
    [
        "../evil.py",
        "a/../../evil.py",
        "a/./b.py",
        "a//b.py",
        "/abs.py",
        "C:/x.py",
        "C:\\x.py",
        "\\\\server\\share\\x.py",
        "//server/share/x.py",
        "a\\..\\..\\x.py",
        "plugin.toml:stream",
        "a/b.py:hidden.txt",
        "a/b\x01.py",
        "CON",
        "con.txt",
        "a/NUL.py",
        "aux.json",
        "COM1.txt",
        "lpt9.md",
        "a/Com5.x.py",
        "a/b.py.",
        "a/b.py ",
        "a/ ./x.py",
        "a/.../x.py",
    ],
)
def test_bad_paths_rejected(env: Env, name: str) -> None:
    data = raw_zip([("plugin.toml", b"x", 0), (name, b"x", 0)])
    with pytest.raises(UserError):
        env.check(data)


def test_trailing_dot_name_is_normalized_not_smuggled(env: Env) -> None:
    # 'x.py.' 在 Windows 上就是 x.py：和另一个 x.py 属于重复条目
    data = raw_zip([("plugin.toml", b"x", 0), ("x.py", b"1", 0), ("x.py.", b"2", 0)])
    rejects(env, data, "重复")


def test_symlink_rejected(env: Env) -> None:
    link = (stat.S_IFLNK | 0o777) << 16
    data = raw_zip([("plugin.toml", b"x", 0), ("a.py", b"target", link)])
    rejects(env, data, "符号链接")


def test_special_file_type_rejected(env: Env) -> None:
    fifo = (stat.S_IFIFO | 0o644) << 16
    rejects(env, raw_zip([("plugin.toml", b"x", 0), ("a.py", b"", fifo)]), "普通文件")


@pytest.mark.parametrize(
    "name", ["a.exe", "lib/x.dll", "x.pyd", "x.so", "run.bat", "n.zip", "x.js"]
)
def test_binary_rejected(env: Env, name: str) -> None:
    files = {**plugin_files(), name: b"MZ"}
    rejects(env, make_zip(files), "二进制")


def test_unknown_suffix_and_no_suffix_rejected(env: Env) -> None:
    rejects(env, make_zip({**plugin_files(), "LICENSE": b"x"}), "不接受")
    rejects(env, make_zip({**plugin_files(), "x.svg": b"<svg/>"}), "不接受")


def test_allowed_types_pass(env: Env) -> None:
    extra = {"data.json": b"{}", "r.md": b"#", "n.txt": b"", "icon.png": b"\x89PNG", "p.jpg": b"x"}
    assert env.check(make_zip({**plugin_files(), **extra}))


def test_nested_zip_rejected(env: Env) -> None:
    inner = make_zip(plugin_files())
    rejects(env, make_zip({**plugin_files(), "inner.zip": inner}), "二进制")


def test_duplicate_and_case_conflict(env: Env) -> None:
    dup = raw_zip([("plugin.toml", b"x", 0), ("a.py", b"1", 0), ("a.py", b"2", 0)])
    rejects(env, dup, "重复条目")
    case = raw_zip([("plugin.toml", b"x", 0), ("a.py", b"1", 0), ("A.PY", b"2", 0)])
    rejects(env, case, "大小写冲突")


def test_file_dir_conflict(env: Env) -> None:
    data = raw_zip([("plugin.toml", b"x", 0), ("a.py", b"1", 0), ("a.py/b.py", b"2", 0)])
    rejects(env, data, "同名")
    data = raw_zip([("plugin.toml", b"x", 0), ("a.py/b.py", b"2", 0), ("a.py", b"1", 0)])
    rejects(env, data, "同名")


def test_zip_bomb_by_ratio(env: Env) -> None:
    big = {**plugin_files(), "zeros.txt": b"\0" * (3 * 1024 * 1024)}
    rejects(env, make_zip(big), "压缩比")


def test_total_bytes_limit_counts_real_bytes(tmp_path: Path) -> None:
    env = Env(tmp_path, Limits(max_total_bytes=10_000, max_ratio=10**9))
    rejects(env, make_zip({**plugin_files(), "a.txt": b"x" * 6000, "b.txt": b"y" * 6000}), "总量")


def test_single_file_limit(tmp_path: Path) -> None:
    env = Env(tmp_path, Limits(max_file_bytes=1000, max_ratio=10**9))
    rejects(env, make_zip({**plugin_files(), "big.txt": b"x" * 2000}), "文件解压后太大")


def test_declared_size_in_header_is_not_trusted(tmp_path: Path) -> None:
    env = Env(tmp_path, Limits(max_file_bytes=1000, max_ratio=10**9))
    data = bytearray(make_zip({"plugin.toml": b"x", "big.txt": b"x" * 2000}))
    # 把头里声明的未压缩大小改小（本地头和中央目录各一处），实际读取仍按真实字节数
    needle = (2000).to_bytes(4, "little")
    patched = bytes(data).replace(needle, (10).to_bytes(4, "little"))
    with pytest.raises(UserError):
        env.check(patched)


def test_file_count_limit(tmp_path: Path) -> None:
    env = Env(tmp_path, Limits(max_files=3))
    files = {**plugin_files(), **{f"f{i}.txt": b"" for i in range(5)}}
    rejects(env, make_zip(files), "文件太多")


def test_path_length_and_depth(tmp_path: Path) -> None:
    env = Env(tmp_path, Limits(max_path_length=30, max_depth=3))
    rejects(env, make_zip({**plugin_files(), "a/b/c/d/e.py": b""}), "层级")
    rejects(env, make_zip({**plugin_files(), "x" * 40 + ".py": b""}), "路径太长")


def test_zip_size_limit_while_streaming(tmp_path: Path) -> None:
    env = Env(tmp_path, Limits(max_zip_bytes=1000))
    chunks = iter([b"x" * 600, b"x" * 600, pytest.fail])  # 第三块不应被读取
    with pytest.raises(UserError) as caught:
        env.installer.inspect(chunks)
    assert caught.value.status_code == 413
    assert not list(env.installer._tmp.glob("upload-*"))


def test_not_a_zip(env: Env) -> None:
    rejects(env, b"this is not a zip", "有效的 zip")


def test_encrypted_entry_rejected(env: Env) -> None:
    data = bytearray(make_zip(plugin_files()))
    flag = data.find(b"PK\x01\x02") + 8
    data[flag] |= 1
    with pytest.raises(UserError):
        env.check(bytes(data))


def test_id_without_namespace_rejected(env: Env) -> None:
    rejects(env, make_zip(plugin_files("hello")), "命名空间")


def test_reserved_namespace_rejected(env: Env) -> None:
    rejects(env, make_zip(plugin_files("mortis.x")), "保留给宿主")


def test_device_name_id_rejected(env: Env) -> None:
    rejects(env, make_zip(plugin_files("con.x")), "保留设备名")


def test_collision_with_builtin_rejected(env: Env) -> None:
    toml = TOML.format(pid="builtin_demo", version="2")
    rejects(env, make_zip(plugin_files("builtin_demo", toml=toml)), "命名空间")
    # 带命名空间的成员 id 和内置插件重名
    toml = TOML.format(pid="me.x", version="1") + '\n[[sources]]\nid = "builtin_demo"\nname = "s"\n'
    rejects(env, make_zip(plugin_files(toml=toml)), "builtin_demo")


def test_collision_with_other_user_plugin_rejected(env: Env) -> None:
    env.installer.confirm(env.check(make_zip(plugin_files("me.one"))).token)
    toml = TOML.format(pid="me.two", version="1") + '\n[[sources]]\nid = "me.one"\nname = "s"\n'
    rejects(env, make_zip(plugin_files(toml=toml)), "重名")


def test_api_version_not_satisfied(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    toml = TOML.format(pid="me.hello", version="1").replace("api_version = 1", "api_version = 99")
    rejects(env, make_zip(plugin_files(toml=toml)), "升级莫提斯")
    toml = TOML.format(pid="me.hello", version="1").replace("api_version = 1", "api_version = 0")
    rejects(env, make_zip(plugin_files(toml=toml)), "太旧")
    toml = TOML.format(pid="me.hello", version="1") + 'min_mortis_version = "999.0"\n'
    monkeypatch.setattr("mortis.plugins.loader.get_version", lambda: "0.2.0")
    rejects(env, make_zip(plugin_files(toml=toml)), "升级莫提斯")


def test_preview_does_not_import_even_when_code_has_side_effects(env: Env, tmp_path: Path) -> None:
    marker = tmp_path / "side.txt"
    preview = env.check(make_zip(plugin_files(marker=str(marker))))
    assert not marker.exists()
    assert not any(k.startswith("mortis_plugins._trial") for k in sys.modules)
    env.installer.confirm(preview.token)
    assert marker.exists()
    assert not any(k.startswith("mortis_plugins._trial") for k in sys.modules)


def test_check_imports_runs_only_after_confirm(env: Env, tmp_path: Path) -> None:
    bad = plugin_files()
    bad["__init__.py"] = b"import ctypes\n"
    preview = env.check(make_zip(bad))  # 预览阶段不跑约定检查
    assert preview.id == "me.hello"
    with pytest.raises(UserError, match="试运行"):
        env.installer.confirm(preview.token)


def test_manifest_code_mismatch_rejected_on_confirm(env: Env) -> None:
    toml = TOML.format(pid="me.hello", version="1") + '\n[[sources]]\nid = "me.ghost"\nname = "s"\n'
    preview = env.check(make_zip(plugin_files(toml=toml)))
    with pytest.raises(UserError, match="试运行"):
        env.installer.confirm(preview.token)


def test_overwrite_shows_diff_and_requires_restart(env: Env, tmp_path: Path) -> None:
    env.installer.confirm(env.check(make_zip(plugin_files(version="1.0"))).token)
    files = plugin_files(version="2.0")
    files["extra.txt"] = b"new"
    preview = env.check(make_zip(files))
    assert preview.existing is not None and preview.needs_restart
    assert preview.existing.version == "1.0" and preview.version == "2.0"
    assert preview.existing.added == ("extra.txt",)
    assert "plugin.toml" in preview.existing.changed
    assert env.registry.get("me.hello").version == "1.0"  # type: ignore[union-attr]
    result = env.installer.confirm(preview.token)
    assert result.replaced and result.needs_restart and result.previous_version == "1.0"
    assert (env.user_dir / "me.hello" / "extra.txt").is_file()
    assert "me.hello" in env.installer.pending_restart
    assert not list(env.user_dir.glob(".*"))


def test_overwrite_rollback_when_second_move_fails(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    env.installer.confirm(env.check(make_zip(plugin_files(version="1.0"))).token)
    before = (env.user_dir / "me.hello" / "plugin.toml").read_text(encoding="utf-8")
    preview = env.check(make_zip(plugin_files(version="2.0")))
    real = packaging._move
    calls: list[Path] = []

    def flaky(source: Path, destination: Path) -> None:
        calls.append(destination)
        if destination.name == "me.hello" and source.name == ".me.hello.new":
            raise OSError("注入失败")
        real(source, destination)

    monkeypatch.setattr(packaging, "_move", flaky)
    with pytest.raises(UserError, match="已恢复旧版本"):
        env.installer.confirm(preview.token)
    monkeypatch.undo()
    folder = env.user_dir / "me.hello"
    assert (folder / "plugin.toml").read_text(encoding="utf-8") == before
    assert (folder / "__init__.py").is_file()
    assert not list(env.user_dir.glob(".*"))
    env.registry.load()
    assert env.registry.get("me.hello").version == "1.0"  # type: ignore[union-attr]


def test_overwrite_rollback_when_first_move_fails(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    env.installer.confirm(env.check(make_zip(plugin_files(version="1.0"))).token)
    preview = env.check(make_zip(plugin_files(version="2.0")))
    real = packaging._move

    def flaky(source: Path, destination: Path) -> None:
        if destination.name == ".me.hello.old":
            raise OSError("占用中")
        real(source, destination)

    monkeypatch.setattr(packaging, "_move", flaky)
    with pytest.raises(UserError):
        env.installer.confirm(preview.token)
    monkeypatch.undo()
    assert "1.0" in (env.user_dir / "me.hello" / "plugin.toml").read_text(encoding="utf-8")
    assert not list(env.user_dir.glob(".*"))


def test_install_refused_if_target_changed_after_preview(env: Env) -> None:
    preview = env.check(make_zip(plugin_files()))
    other = env.check(make_zip(plugin_files(version="9")))
    env.installer.confirm(other.token)
    with pytest.raises(UserError, match="重新上传"):
        env.installer.confirm(preview.token)


def test_fresh_install_reload_failure_removes_new_folder(env: Env) -> None:
    def boom() -> None:
        raise RuntimeError("加载炸了")

    env.installer.reload = boom
    preview = env.check(make_zip(plugin_files()))
    with pytest.raises(UserError, match="已撤回"):
        env.installer.confirm(preview.token)
    assert not (env.user_dir / "me.hello").exists()


def test_uninstall_user_plugin_keeps_plugin_data(env: Env) -> None:
    env.installer.confirm(env.check(make_zip(plugin_files())).token)
    data = env.data / "plugin-data" / "me.hello"
    data.mkdir(parents=True)
    (data / "state.json").write_text("{}", encoding="utf-8")
    assert env.installer.uninstall("me.hello") == ("me.hello", "示例")
    assert not (env.user_dir / "me.hello").exists()
    assert env.registry.get("me.hello") is None
    assert (data / "state.json").is_file()


def test_uninstall_builtin_rejected(env: Env) -> None:
    with pytest.raises(UserError) as caught:
        env.installer.uninstall("builtin_demo")
    assert caught.value.status_code == 403
    assert (env.builtin / "builtin_demo" / "plugin.toml").is_file()


def test_uninstall_unknown_and_child_rejected(env: Env) -> None:
    with pytest.raises(UserError) as caught:
        env.installer.uninstall("me.none")
    assert caught.value.status_code == 404
    toml = TOML.format(pid="me.pack", version="1") + (
        '\n[[sources]]\nid = "me.inner"\nname = "s"\n'
    )
    code = (
        CODE.format(pid="me.inner", marker=default_marker()).replace(
            "PLUGIN = Src()",
            "from mortis.plugin_api import Plugin\nPLUGIN = Plugin(sources=(Src(),))",
        )
    ).encode()
    files = {"plugin.toml": toml.encode(), "__init__.py": code}
    env.installer.confirm(env.check(make_zip(files)).token)
    with pytest.raises(UserError):
        env.installer.uninstall("me.inner")
    env.installer.uninstall("me.pack")


def test_manual_plugin_without_mortis_table_is_load_failure(env: Env) -> None:
    folder = env.user_dir / "me.manual"
    folder.mkdir(parents=True)
    (folder / "plugin.toml").write_text(
        'id = "me.manual"\nname = "x"\nversion = "1"\n', encoding="utf-8"
    )
    (folder / "__init__.py").write_text("PLUGIN = None\n", encoding="utf-8")
    env.registry.load()
    info = env.registry.get("me.manual")
    assert info is not None and not info.active and info.error is not None
    assert "[mortis]" in info.error


# ---------------------------------------------------------------- HTTP 接口


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    builtin = tmp_path / "builtin"
    builtin.mkdir()
    settings = Settings(data_dir=tmp_path / "data", plugins_dir=builtin, web_dist=None)
    return TestClient(create_app(settings), base_url="http://127.0.0.1")


def upload(client: TestClient, data: bytes) -> dict:  # type: ignore[type-arg]
    response = client.post(
        "/api/plugins/import/check", content=data, headers={"Content-Type": "application/zip"}
    )
    assert response.status_code == 200, response.text
    return response.json()  # type: ignore[no-any-return]


def test_http_two_step_install_and_uninstall(client: TestClient, tmp_path: Path) -> None:
    marker = tmp_path / "m.txt"
    preview = upload(client, make_zip(plugin_files(marker=str(marker))))
    assert not marker.exists()
    assert preview["id"] == "me.hello" and preview["existing"] is None
    assert preview["capabilities"] == ["source"]
    assert {f["path"] for f in preview["files"]} == {"plugin.toml", "__init__.py"}
    refused = client.post(
        "/api/plugins/import/confirm", json={"token": preview["token"], "trust": False}
    )
    assert refused.status_code == 422
    done = client.post(
        "/api/plugins/import/confirm", json={"token": preview["token"], "trust": True}
    )
    assert done.status_code == 200, done.text
    body = done.json()
    assert body["id"] == "me.hello" and body["replaced"] is False and body["needs_restart"] is False
    assert marker.exists()
    listed = {p["id"]: p for p in client.get("/api/plugins").json()}
    assert listed["me.hello"]["origin"] == "user" and listed["me.hello"]["error"] is None
    again = client.post(
        "/api/plugins/import/confirm", json={"token": preview["token"], "trust": True}
    )
    assert again.status_code == 410
    assert client.delete("/api/plugins/me.hello").status_code == 200
    assert "me.hello" not in {p["id"] for p in client.get("/api/plugins").json()}


def test_http_overwrite_marks_restart_pending(client: TestClient) -> None:
    first = upload(client, make_zip(plugin_files(version="1.0")))
    client.post("/api/plugins/import/confirm", json={"token": first["token"], "trust": True})
    second = upload(client, make_zip(plugin_files(version="2.0")))
    assert second["existing"]["version"] == "1.0" and second["needs_restart"] is True
    done = client.post(
        "/api/plugins/import/confirm", json={"token": second["token"], "trust": True}
    ).json()
    assert done["replaced"] and done["needs_restart"] and done["previous_version"] == "1.0"
    listed = {p["id"]: p for p in client.get("/api/plugins").json()}
    assert listed["me.hello"]["restart_pending"] is True


def test_http_rejects_bad_zip_with_message(client: TestClient) -> None:
    response = client.post(
        "/api/plugins/import/check",
        content=make_zip({"plugin.toml": b'id = "me.x"\nname = "x"\nversion = "1"\n'}),
    )
    assert response.status_code == 422
    assert "这不是莫提斯插件包" in response.json()["detail"]


def test_http_uninstall_builtin_refused(tmp_path: Path) -> None:
    builtin = tmp_path / "builtin" / "builtin_demo"
    builtin.mkdir(parents=True)
    (builtin / "plugin.toml").write_text(
        TOML.format(pid="builtin_demo", version="1"), encoding="utf-8"
    )
    (builtin / "__init__.py").write_text(
        CODE.format(pid="builtin_demo", marker=default_marker()), encoding="utf-8"
    )
    settings = Settings(data_dir=tmp_path / "data", plugins_dir=tmp_path / "builtin", web_dist=None)
    client = TestClient(create_app(settings), base_url="http://127.0.0.1")
    assert client.delete("/api/plugins/builtin_demo").status_code == 403
