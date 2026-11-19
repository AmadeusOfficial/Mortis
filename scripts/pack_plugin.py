"""把一个插件目录校验并打成可以在设置页导入的 zip。

    python scripts/pack_plugin.py <插件目录> [-o <输出目录或 .zip 文件>]

校验用的就是宿主导入时的那套规则（同一份代码），打包前自己先过一遍：
plugin.toml 的清单、import 约定检查、zip 的路径和文件类型白名单、各项大小限额，
最后在临时目录里用注册表真的加载一次，确认清单和 PLUGIN 对象逐项对得上。
这一步会执行你自己插件的代码；它和导入时的「试运行」一样，不是安全检查。

zip 里不放 tests 目录、test_*.py、__pycache__ 和以点开头的文件。
同样的内容打出来的 zip 字节完全一样（固定了时间戳和排序）。
"""

import argparse
import hashlib
import re
import stat
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

from mortis.errors import UserError
from mortis.plugins.loader import (
    MANIFEST,
    PluginLoadError,
    _is_test_file,
    check_imports,
    has_namespace,
    read_manifest,
)
from mortis.plugins.packaging import PluginInstaller, Preview
from mortis.plugins.registry import PluginRegistry
from mortis.services import KeyedLocks

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = Path("dist")
FIXED_TIME = (1980, 1, 1, 0, 0, 0)
"""zip 条目的时间戳固定下来，内容不变时打出的 zip 也不变。"""


class PackError(Exception):
    """打包失败。消息直接给插件作者看。"""


@dataclass(frozen=True)
class PackResult:
    path: Path
    plugin_id: str
    name: str
    version: str
    file_count: int
    total_size: int
    package_hash: str
    """和设置页导入预览里显示的包哈希是同一个值。"""
    zip_sha256: str
    capabilities: tuple[str, ...]


def _skipped(relative: Path) -> bool:
    return (
        any(part.startswith(".") or part == "__pycache__" for part in relative.parts)
        or relative.suffix == ".pyc"
        or _is_test_file(relative)
    )


def collect_files(folder: Path) -> list[Path]:
    """要放进 zip 的文件（相对插件目录），按路径排序。"""
    found = [
        path.relative_to(folder)
        for path in folder.rglob("*")
        if path.is_file() and not _skipped(path.relative_to(folder))
    ]
    return sorted(found, key=lambda p: p.as_posix())


def build_zip(folder: Path, files: list[Path], target: Path) -> None:
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for relative in files:
            info = zipfile.ZipInfo(relative.as_posix(), FIXED_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            archive.writestr(info, (folder / relative).read_bytes())


def _preflight(folder: Path) -> str:
    """打包前先查作者最常犯的错，返回插件 id。"""
    if not folder.is_dir():
        raise PackError(f"找不到插件目录：{folder}")
    if not (folder / MANIFEST).is_file():
        raise PackError(
            f"目录里没有 {MANIFEST}。要指向插件目录本身，plugin.toml 和 __init__.py 直接放在里面"
        )
    if not (folder / "__init__.py").is_file():
        raise PackError("目录里没有 __init__.py，插件要在里面定义 PLUGIN")
    try:
        manifest = read_manifest(folder)
    except PluginLoadError as error:
        raise PackError(f"{MANIFEST} 有问题：{error}") from None
    if not has_namespace(manifest.id):
        raise PackError(
            f"插件 id「{manifest.id}」要带命名空间，写成「作者.名字」，比如 me.{manifest.id}；"
            "不带点的 id 留给程序自带的插件，导入时会被拒绝。id 定下后就不要再改"
        )
    try:
        check_imports(folder)
    except PluginLoadError as error:
        raise PackError(
            f"代码不符合插件约定：{error}。"
            "插件只能 import 标准库、pydantic 和 mortis.plugin_api，开进程和联网要经宿主"
        ) from None
    return manifest.id


def _inspect(installer: PluginInstaller, archive: Path) -> Preview:
    def chunks():
        with archive.open("rb") as handle:
            while chunk := handle.read(64 * 1024):
                yield chunk

    try:
        return installer.inspect(chunks())
    except UserError as error:
        raise PackError(f"导入检查没通过：{error.message}") from None


def _output_path(output: Path | None, plugin_id: str, version: str) -> Path:
    if output is not None and output.suffix.lower() == ".zip":
        return output
    safe = re.sub(r"[^\w.+-]", "_", version)
    return (output or DEFAULT_OUTPUT) / f"{plugin_id}-{safe}.zip"


def pack(folder: Path, output: Path | None = None) -> PackResult:
    """校验并打包，成功返回结果，失败抛 PackError。"""
    folder = folder.resolve()
    plugin_id = _preflight(folder)
    files = collect_files(folder)
    loaded_before = set(sys.modules)
    with tempfile.TemporaryDirectory(prefix="mortis-pack-", ignore_cleanup_errors=True) as work:
        work_dir = Path(work)
        staging = work_dir / "plugin.zip"
        build_zip(folder, files, staging)
        user_dir = work_dir / "data" / "plugins"
        folders: list[tuple[Path, str]] = []
        if (ROOT / "plugins").is_dir():
            folders.append((ROOT / "plugins", "builtin"))
        folders.append((user_dir, "user"))
        registry = PluginRegistry(folders)  # type: ignore[arg-type]
        registry.load()
        installer = PluginInstaller(user_dir, registry, KeyedLocks(), registry.load)
        try:
            preview = _inspect(installer, staging)
            try:
                installer.confirm(preview.token)
            except UserError as error:
                raise PackError(f"试运行没通过：{error.message}") from None
            info = registry.get(plugin_id)
            if info is None or info.error:
                raise PackError(f"试加载失败：{info.error if info else '没有出现在注册表里'}")
        finally:
            for key in set(sys.modules) - loaded_before:
                if key.startswith("mortis_plugins"):
                    del sys.modules[key]
        target = _output_path(output, plugin_id, preview.version)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(staging.read_bytes())
    return PackResult(
        path=target,
        plugin_id=plugin_id,
        name=preview.name,
        version=preview.version,
        file_count=len(preview.files),
        total_size=preview.total_size,
        package_hash=preview.package_hash,
        zip_sha256=hashlib.sha256(target.read_bytes()).hexdigest(),
        capabilities=preview.capabilities,
    )


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    parser = argparse.ArgumentParser(description="校验插件目录并打成可导入的 zip")
    parser.add_argument("folder", type=Path, help="插件目录（里面直接放 plugin.toml）")
    parser.add_argument(
        "-o", "--output", type=Path, help="输出目录，或以 .zip 结尾的文件名（默认 ./dist）"
    )
    args = parser.parse_args(argv)
    try:
        result = pack(args.folder, args.output)
    except PackError as error:
        print(f"打包失败：{error}", file=sys.stderr)
        return 1
    print(f"插件：{result.plugin_id}（{result.name}）{result.version}")
    print(f"提供：{'、'.join(result.capabilities) or '无'}")
    print(f"文件：{result.file_count} 个，共 {result.total_size} 字节")
    print(f"输出：{result.path}")
    print(f"包哈希：{result.package_hash}（导入预览里显示的就是它）")
    print(f"zip SHA-256：{result.zip_sha256}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
