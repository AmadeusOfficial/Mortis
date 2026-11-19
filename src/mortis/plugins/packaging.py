"""插件 zip 的导入和卸载。

两步走：`Upload.finish()` 只做静态检查（流式接收、路径和类型检查、限额、只读 plugin.toml），
把包解到私有临时目录，返回预览和一次性确认令牌，**不执行插件的任何代码**。
`PluginInstaller.confirm()` 凭令牌才会试运行（check_imports 加 import 一遍）并安装。
check_imports 只是约定检查，不是沙箱，导入的插件仍是在本机运行的 Python 代码。

目录替换在 Windows 下不是单次原子操作：先把旧目录改成 `.old`，再把新目录改成正式名，
任何一步失败都把旧目录改回去。覆盖升级后旧对象还留在内存里，要重启莫提斯才完全换成新版。
"""

import hashlib
import logging
import re
import secrets
import shutil
import stat
import sys
import threading
import time
import tomllib
import uuid
import zipfile
import zlib
from collections.abc import Callable, Iterable
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass, replace
from pathlib import Path
from typing import IO, Any

from mortis.errors import UserError
from mortis.plugins.loader import (
    MANIFEST,
    PACKAGE,
    Manifest,
    PluginLoadError,
    check_imports,
    has_namespace,
    import_plugin,
    read_manifest,
)
from mortis.plugins.registry import PluginRegistry, _manifest_capabilities, _validate
from mortis.services import KeyedLocks

_LOG = logging.getLogger(__name__)

TEMP_FOLDER = ".plugin-tmp"
INSTALL_LOCK = "plugin-install"
CATALOG_LOCK = "catalog-write"
CHUNK = 64 * 1024
ALLOWED_SUFFIXES = frozenset(
    {".py", ".json", ".md", ".txt", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico"}
)
BINARY_SUFFIXES = frozenset(
    {
        ".exe", ".dll", ".pyd", ".so", ".dylib", ".bat", ".cmd", ".com", ".msi", ".scr",
        ".ps1", ".vbs", ".js", ".jar", ".sh", ".lnk", ".zip", ".7z", ".rar", ".gz", ".tar", ".whl",
    }
)  # fmt: skip
"""出现这些直接拒绝并点名；白名单之外的其他后缀也拒绝，只是提示不同。"""
_RESERVED = frozenset({"con", "prn", "aux", "nul", "conin$", "conout$"})
_RESERVED_NUMBERED = re.compile(r"^(com|lpt)[1-9\u00b9\u00b2\u00b3]$")
_SKIPPED_TOP = frozenset({"__macosx"})
_SKIPPED_NAMES = frozenset({".ds_store", "thumbs.db"})


@dataclass(frozen=True)
class Limits:
    max_zip_bytes: int = 20 * 1024 * 1024
    max_file_bytes: int = 5 * 1024 * 1024
    max_total_bytes: int = 30 * 1024 * 1024
    max_files: int = 500
    max_path_length: int = 200
    max_depth: int = 8
    max_ratio: int = 100
    """单个文件解压后超过 ratio_floor 字节时，解压大小与压缩大小之比的上限；总量按整包再算一次。"""
    ratio_floor: int = 64 * 1024
    token_ttl: float = 600.0
    max_pending: int = 8


# ---------------------------------------------------------------- 路径检查


def _reserved_device(segment: str) -> bool:
    stem = segment.split(".", 1)[0].rstrip(" ")
    return stem in _RESERVED or bool(_RESERVED_NUMBERED.fullmatch(stem))


def normalize_entry(raw: str, limits: Limits) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """规范化一个 zip 条目名，返回 (落盘各段, 去重用各段)；不合格抛 UserError。

    Windows 会把段末尾的点和空格吃掉、不分大小写，所以去重和保留名都按处理后的写法比较。
    """
    path = raw.replace("\\", "/")
    shown = raw
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in path):
        raise UserError(f"条目名含控制字符：{shown!r}")
    if ":" in path:
        raise UserError(f"条目名含冒号（盘符或备用数据流）：{shown}")
    if path.startswith("/"):
        raise UserError(f"条目是绝对路径或网络路径：{shown}")
    if path.endswith("/"):
        path = path[:-1]
    if len(path) > limits.max_path_length:
        raise UserError(f"条目路径太长（上限 {limits.max_path_length}）：{shown[:60]}…")
    parts = path.split("/")
    if len(parts) > limits.max_depth:
        raise UserError(f"目录层级太深（上限 {limits.max_depth}）：{shown}")
    real: list[str] = []
    keys: list[str] = []
    for part in parts:
        if part in {"", ".", ".."}:
            raise UserError(f"条目路径含空段、. 或 ..：{shown}")
        trimmed = part.rstrip(". ")
        if trimmed == "":
            raise UserError(f"条目路径的段只有点或空格：{shown}")
        key = trimmed.casefold()
        if _reserved_device(key):
            raise UserError(f"条目用了 Windows 保留设备名：{shown}")
        real.append(trimmed)
        keys.append(key)
    return tuple(real), tuple(keys)


def _entry_kind(info: zipfile.ZipInfo) -> str:
    """'dir' 或 'file'；符号链接和其他特殊类型直接拒绝。"""
    mode = info.external_attr >> 16
    file_type = stat.S_IFMT(mode)  # 有的打包工具只写权限位、不写类型位，当普通文件
    if file_type:
        if stat.S_ISLNK(mode):
            raise UserError(f"不接受符号链接：{info.filename}")
        if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
            raise UserError(f"只接受普通文件和目录：{info.filename}")
    if info.external_attr & 0x400:
        raise UserError(f"不接受重解析点（符号链接或联接）：{info.filename}")
    if info.flag_bits & 0x1:
        raise UserError(f"不接受加密条目：{info.filename}")
    if info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
        raise UserError(f"不支持的压缩方式：{info.filename}")
    is_dir = info.is_dir() or info.filename.replace("\\", "/").endswith("/")
    if is_dir and file_type and not stat.S_ISDIR(mode):
        raise UserError(f"目录条目的类型不对：{info.filename}")
    return "dir" if is_dir else "file"


def _check_file_type(parts: tuple[str, ...]) -> None:
    name = parts[-1]
    lowered = name.casefold()
    suffix = Path(lowered).suffix
    if lowered == MANIFEST and len(parts) <= 2:
        return
    if suffix in BINARY_SUFFIXES:
        raise UserError(f"不接受二进制或可执行文件：{'/'.join(parts)}")
    if suffix not in ALLOWED_SUFFIXES:
        raise UserError(
            f"不接受这种文件：{'/'.join(parts)}"
            "（只收 .py、plugin.toml、.json、.md、.txt 和常见图片）"
        )


@dataclass(frozen=True)
class _Entry:
    info: zipfile.ZipInfo
    kind: str
    parts: tuple[str, ...]
    keys: tuple[str, ...]


def _plan(infos: list[zipfile.ZipInfo], limits: Limits) -> tuple[list[_Entry], tuple[str, ...]]:
    """逐条检查路径、类型、重复和冲突，决定包根；返回要解的条目（路径已去掉包根）。"""
    if len(infos) > limits.max_files + 100:
        raise UserError(f"条目太多（上限 {limits.max_files} 个文件）")
    entries: list[_Entry] = []
    seen_files: dict[tuple[str, ...], str] = {}
    seen_dirs: set[tuple[str, ...]] = set()
    for info in infos:
        kind = _entry_kind(info)
        parts, keys = normalize_entry(info.filename, limits)
        if keys[0] in _SKIPPED_TOP or keys[-1] in _SKIPPED_NAMES:
            continue
        for depth in range(1, len(keys)):
            prefix = keys[:depth]
            if prefix in seen_files:
                raise UserError(f"文件和目录同名：{'/'.join(parts[:depth])}")
            seen_dirs.add(prefix)
        if kind == "dir":
            if keys in seen_files:
                raise UserError(f"文件和目录同名：{'/'.join(parts)}")
            seen_dirs.add(keys)
        else:
            _check_file_type(parts)
            if keys in seen_dirs:
                raise UserError(f"文件和目录同名：{'/'.join(parts)}")
            if keys in seen_files:
                same = seen_files[keys] == "/".join(parts)
                raise UserError(f"{'重复条目' if same else '大小写冲突的条目'}：{'/'.join(parts)}")
            seen_files[keys] = "/".join(parts)
        entries.append(_Entry(info, kind, parts, keys))
    files = [e for e in entries if e.kind == "file"]
    if not files:
        raise UserError("压缩包里没有文件")
    if len(files) > limits.max_files:
        raise UserError(f"文件太多（上限 {limits.max_files} 个）")
    return _strip_root(entries)


def _strip_root(entries: list[_Entry]) -> tuple[list[_Entry], tuple[str, ...]]:
    files = [e for e in entries if e.kind == "file"]
    if any(e.keys == (MANIFEST,) for e in files):
        return entries, ()
    tops = {e.keys[0] for e in entries}
    if len(tops) == 1 and not any(len(e.keys) == 1 and e.kind == "file" for e in entries):
        top = entries[0].parts[0]
        if any(e.keys[1:] == (MANIFEST,) for e in files):
            kept = [
                _Entry(e.info, e.kind, e.parts[1:], e.keys[1:]) for e in entries if len(e.parts) > 1
            ]
            return kept, (top,)
    raise UserError("压缩包根目录要有 plugin.toml，或者只有一个顶层文件夹、里面有 plugin.toml")


# ---------------------------------------------------------------- 解压和哈希


@dataclass(frozen=True)
class FileEntry:
    path: str
    size: int
    sha256: str


def package_hash(files: Iterable[FileEntry]) -> str:
    digest = hashlib.sha256()
    for entry in sorted(files, key=lambda item: item.path):
        digest.update(f"{entry.path}\t{entry.sha256}\n".encode())
    return digest.hexdigest()


def folder_files(folder: Path) -> list[FileEntry]:
    """已安装目录的文件清单，用来和新包比较；__pycache__ 不算。"""
    found: list[FileEntry] = []
    for path in sorted(folder.rglob("*")):
        relative = path.relative_to(folder)
        if "__pycache__" in relative.parts or not path.is_file():
            continue
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(CHUNK), b""):
                digest.update(chunk)
        found.append(FileEntry(relative.as_posix(), path.stat().st_size, digest.hexdigest()))
    return found


def _extract(
    archive: zipfile.ZipFile, entries: list[_Entry], dest: Path, limits: Limits, zip_size: int
) -> list[FileEntry]:
    """逐块解压，按实际读到的字节计数，不信 zip 头里的大小。"""
    total = 0
    written: list[FileEntry] = []
    dest.mkdir(parents=True)
    root = dest.resolve()
    for entry in entries:
        target = dest.joinpath(*entry.parts)
        if root not in target.resolve().parents and target.resolve() != root:
            raise UserError(f"条目落到了目录之外：{entry.info.filename}")
        if entry.kind == "dir":
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256()
        size = 0
        with archive.open(entry.info) as source, target.open("xb") as out:
            while chunk := source.read(CHUNK):
                size += len(chunk)
                total += len(chunk)
                if size > limits.max_file_bytes:
                    raise UserError(
                        f"文件解压后太大（上限 {limits.max_file_bytes // 1024 // 1024} MB）："
                        f"{'/'.join(entry.parts)}"
                    )
                if total > limits.max_total_bytes:
                    raise UserError(
                        f"解压后总量太大（上限 {limits.max_total_bytes // 1024 // 1024} MB）"
                    )
                if (
                    size > limits.ratio_floor
                    and size > max(entry.info.compress_size, 1) * limits.max_ratio
                ):
                    raise UserError(f"压缩比异常（疑似压缩炸弹）：{'/'.join(entry.parts)}")
                digest.update(chunk)
                out.write(chunk)
        written.append(FileEntry("/".join(entry.parts), size, digest.hexdigest()))
    if total > limits.ratio_floor and total > max(zip_size, 1) * limits.max_ratio:
        raise UserError("整包压缩比异常（疑似压缩炸弹）")
    return written


# ---------------------------------------------------------------- 预览和令牌


@dataclass(frozen=True)
class ExistingInfo:
    id: str
    name: str
    version: str
    package_hash: str
    added: tuple[str, ...]
    removed: tuple[str, ...]
    changed: tuple[str, ...]


@dataclass(frozen=True)
class Preview:
    token: str
    expires_in: int
    id: str
    name: str
    version: str
    api_version: int
    min_mortis_version: str | None
    capabilities: tuple[str, ...]
    sources: tuple[tuple[str, str, str], ...]
    players: tuple[tuple[str, str], ...]
    hooks: tuple[Any, ...]
    files: tuple[FileEntry, ...]
    total_size: int
    package_hash: str
    existing: ExistingInfo | None
    needs_restart: bool


@dataclass(frozen=True)
class InstallResult:
    id: str
    name: str
    version: str
    previous_version: str | None
    replaced: bool
    needs_restart: bool


@dataclass
class _Staged:
    plugin_id: str
    root: Path
    package: Path
    package_hash: str
    target: Path
    existing_hash: str | None
    deadline: float


def _remove(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)


def _move(source: Path, destination: Path) -> None:
    """目录改名。单独成函数，测试里注入失败用。"""
    source.rename(destination)


@dataclass
class Upload:
    """正在接收的一个 zip：write 流式写临时文件，finish 做静态检查并返回预览。"""

    installer: PluginInstaller
    path: Path
    handle: IO[bytes]
    size: int = 0
    closed: bool = False

    def write(self, chunk: bytes) -> None:
        self.size += len(chunk)
        if self.size > self.installer.limits.max_zip_bytes:
            self.abort()
            raise UserError(
                f"压缩包太大（上限 {self.installer.limits.max_zip_bytes // 1024 // 1024} MB）",
                status_code=413,
            )
        self.handle.write(chunk)

    def abort(self) -> None:
        if not self.closed:
            self.closed = True
            self.handle.close()
        self.path.unlink(missing_ok=True)

    def finish(self) -> Preview:
        if not self.closed:
            self.closed = True
            self.handle.close()
        try:
            return self.installer._inspect_file(self.path, self.size)
        finally:
            self.path.unlink(missing_ok=True)


class PluginInstaller:
    def __init__(
        self,
        user_dir: Path,
        registry: PluginRegistry,
        locks: KeyedLocks,
        reload: Callable[[], None],
        *,
        limits: Limits | None = None,
        clock: Callable[[], float] = time.monotonic,
        pause: Callable[[str], AbstractContextManager[bool]] | None = None,
    ) -> None:
        self.pause = pause
        """按插件暂停任务的入口（TaskRunner.pause_plugin）；覆盖升级和卸载时用。"""
        self.user_dir = user_dir
        self.registry = registry
        self.locks = locks
        self.reload = reload
        self.limits = limits or Limits()
        self._clock = clock
        self._tmp = user_dir.parent / TEMP_FOLDER
        self._guard = threading.Lock()
        self._staged: dict[str, _Staged] = {}
        self.pending_restart: set[str] = set()
        _remove(self._tmp)

    def _paused(self, plugin_id: str) -> AbstractContextManager[bool]:
        """没接任务运行器（测试、脚本）时什么也不暂停。"""
        if self.pause is None:
            return nullcontext(True)
        return self.pause(plugin_id)

    # -- 接收和检查

    def begin_upload(self) -> Upload:
        self._tmp.mkdir(parents=True, exist_ok=True)
        path = self._tmp / f"upload-{uuid.uuid4().hex}.zip"
        return Upload(self, path, path.open("wb"))

    def inspect(self, chunks: Iterable[bytes]) -> Preview:
        upload = self.begin_upload()
        try:
            for chunk in chunks:
                upload.write(chunk)
        except BaseException:
            upload.abort()
            raise
        return upload.finish()

    def _expire(self) -> None:
        now = self._clock()
        with self._guard:
            for token in [t for t, s in self._staged.items() if s.deadline <= now]:
                _remove(self._staged.pop(token).root)
            while len(self._staged) >= self.limits.max_pending:
                oldest = min(self._staged, key=lambda t: self._staged[t].deadline)
                _remove(self._staged.pop(oldest).root)

    def _inspect_file(self, path: Path, size: int) -> Preview:
        self._expire()
        limits = self.limits
        root = self._tmp / f"stage-{uuid.uuid4().hex}"
        try:
            try:
                archive = zipfile.ZipFile(path)
            except zipfile.BadZipFile, OSError:
                raise UserError("这不是有效的 zip 文件") from None
            with archive:
                entries, _ = _plan(archive.infolist(), limits)
                files = _extract(archive, entries, root / "pkg", limits, size)
            package = root / "pkg"
            manifest = self._read_static(package)
            target, existing_info = self._resolve_target(manifest)
            if existing_info is not None:
                existing_info = self._compare(existing_info, target, files)
            digest = package_hash(files)
            token = secrets.token_urlsafe(32)
            existing_hash = existing_info.package_hash if existing_info else None
            staged = _Staged(
                manifest.id, root, package, digest, target, existing_hash,
                self._clock() + limits.token_ttl,
            )  # fmt: skip
            with self._guard:
                self._staged[token] = staged
        except zipfile.BadZipFile, zlib.error, NotImplementedError, RuntimeError, EOFError:
            _remove(root)
            raise UserError("压缩包已损坏或用了不支持的格式") from None
        except BaseException:
            _remove(root)
            raise
        return Preview(
            token=token,
            expires_in=int(limits.token_ttl),
            id=manifest.id,
            name=manifest.name,
            version=manifest.version,
            api_version=manifest.api_version,
            min_mortis_version=manifest.min_mortis_version,
            capabilities=tuple(_manifest_capabilities(manifest)),
            sources=tuple((s.id, s.name, s.data_type) for s in manifest.sources),
            players=tuple((p.id, p.name) for p in manifest.players),
            hooks=manifest.hooks,
            files=tuple(sorted(files, key=lambda item: item.path)),
            total_size=sum(item.size for item in files),
            package_hash=digest,
            existing=existing_info,
            needs_restart=existing_info is not None,
        )

    def _read_static(self, package: Path) -> Manifest:
        """只读 plugin.toml，不执行代码。"""
        try:
            data = tomllib.loads((package / MANIFEST).read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
            raise UserError(f"{MANIFEST} 读不了：{error}") from None
        if not isinstance(data.get("mortis"), dict):
            raise UserError("这不是莫提斯插件包：plugin.toml 里没有 [mortis] 表")
        try:
            manifest = read_manifest(package)
        except PluginLoadError as error:
            raise UserError(str(error)) from None
        if not has_namespace(manifest.id):
            raise UserError(
                f"插件 id「{manifest.id}」要带命名空间（作者.名字）；不带点的 id 留给内置插件"
            )
        for part in manifest.id.split("."):
            if _reserved_device(part):
                raise UserError(f"插件 id「{manifest.id}」用了 Windows 保留设备名")
        return manifest

    def _resolve_target(self, manifest: Manifest) -> tuple[Path, ExistingInfo | None]:
        own = {manifest.id}
        wanted = [
            manifest.id,
            *(s.id for s in manifest.sources),
            *(p.id for p in manifest.players),
            *(h.id for h in manifest.hooks),
        ]
        current = self.registry.get(manifest.id)
        if current is not None and current.origin == "builtin":
            raise UserError(f"插件 id「{manifest.id}」和内置插件重名")
        for info in self.registry.all():
            if info.id in own or info.parent in own:
                continue
            if info.id in wanted:
                owner = "内置插件" if info.origin == "builtin" else "插件"
                raise UserError(f"id「{info.id}」和{owner}「{info.name}」重名")
        target = self.user_dir / manifest.id
        if current is not None and current.parent is None:
            target = current.folder
        if not target.exists():
            return target, None
        old_files = {f.path: f for f in folder_files(target)}
        return target, ExistingInfo(
            id=manifest.id,
            name=current.name if current else manifest.id,
            version=current.version if current else "",
            package_hash=package_hash(old_files.values()),
            added=(),
            removed=(),
            changed=(),
        )

    def _compare(
        self, existing: ExistingInfo, target: Path, new: Iterable[FileEntry]
    ) -> ExistingInfo:
        old = {f.path: f.sha256 for f in folder_files(target)}
        fresh = {f.path: f.sha256 for f in new}
        return replace(
            existing,
            added=tuple(sorted(set(fresh) - set(old))),
            removed=tuple(sorted(set(old) - set(fresh))),
            changed=tuple(sorted(p for p in set(old) & set(fresh) if old[p] != fresh[p])),
        )

    # -- 确认安装

    def take(self, token: str) -> _Staged:
        """令牌一次性：取出就作废，不管后面成不成功。"""
        self._expire()
        with self._guard:
            staged = self._staged.pop(token, None)
        if staged is None:
            raise UserError("确认令牌无效或已过期，请重新上传", status_code=410)
        return staged

    def confirm(self, token: str) -> InstallResult:
        staged = self.take(token)
        try:
            with self.locks.hold(INSTALL_LOCK):
                result = self._install(staged)
        finally:
            _remove(staged.root)
        return result

    def _trial_import(self, staged: _Staged) -> Manifest:
        """用户确认之后：约定检查加真的 import 一遍。用试运行用的模块名，不碰已加载的同 id 插件。"""
        try:
            check_imports(staged.package)
            manifest = read_manifest(staged.package)
            trial = replace(manifest, id=f"_trial_{uuid.uuid4().hex[:8]}", folder=staged.package)
            name = f"{PACKAGE}.{trial.id}"
            try:
                module = import_plugin(trial)
                _validate(manifest, getattr(module, "PLUGIN", None))
            finally:
                for key in [k for k in sys.modules if k == name or k.startswith(f"{name}.")]:
                    del sys.modules[key]
        except PluginLoadError as error:
            raise UserError(f"试运行没通过：{error}") from None
        for cache in staged.package.rglob("__pycache__"):
            _remove(cache)
        return manifest

    def _install(self, staged: _Staged) -> InstallResult:
        current_hash = package_hash(folder_files(staged.target)) if staged.target.exists() else None
        if current_hash != staged.existing_hash:
            raise UserError("预览之后插件目录变了，请重新上传")
        if package_hash(folder_files(staged.package)) != staged.package_hash:
            raise UserError("暂存的文件被改动过，请重新上传")
        manifest = self._trial_import(staged)
        previous = self.registry.get(manifest.id)
        replaced = staged.existing_hash is not None
        if replaced:
            # 先停这个插件的新钩子任务、等旧的跑完，再拿目录写锁换文件；出错也会恢复。
            with self._paused(manifest.id), self.locks.hold(CATALOG_LOCK):
                self._swap(staged)
            self.pending_restart.add(manifest.id)
        else:
            self._swap(staged)
            try:
                self.reload()
            except Exception as error:
                _remove(staged.target)
                _LOG.exception("装好 %s 后重新加载失败，已撤回", manifest.id)
                raise UserError(f"安装后加载失败，已撤回：{error}") from None
        return InstallResult(
            id=manifest.id,
            name=manifest.name,
            version=manifest.version,
            previous_version=previous.version if previous else None,
            replaced=replaced,
            needs_restart=replaced,
        )

    def _swap(self, staged: _Staged) -> None:
        """先把旧目录改成 .old，再把新目录改成正式名；失败就改回去。"""
        target = staged.target
        self.user_dir.mkdir(parents=True, exist_ok=True)
        new = target.with_name(f".{target.name}.new")
        old = target.with_name(f".{target.name}.old")
        _remove(new)
        if old.exists() and not target.exists():
            _move(old, target)  # 上次中途断掉：先把旧的放回去
        _remove(old)
        try:
            _move(staged.package, new)
        except OSError as error:
            raise UserError(f"放进插件目录失败：{error}") from None
        moved_old = False
        try:
            if target.exists():
                _move(target, old)
                moved_old = True
            _move(new, target)
        except OSError as error:
            if moved_old and not target.exists():
                try:
                    _move(old, target)
                except OSError:
                    _LOG.exception("恢复旧目录失败：%s", old)
            _remove(new)
            raise UserError(f"替换插件目录失败，已恢复旧版本：{error}") from None
        _remove(old)

    # -- 卸载

    def uninstall(self, plugin_id: str) -> tuple[str, str]:
        with self.locks.hold(INSTALL_LOCK):
            info = self.registry.get(plugin_id)
            if info is None:
                raise UserError("插件不存在", status_code=404)
            if info.origin != "user":
                raise UserError("内置插件不能卸载，只能停用", status_code=403)
            if info.parent is not None:
                raise UserError(f"这是 {info.parent} 带的条目，要卸载请卸载整个包", status_code=422)
            folder = info.folder
            if folder.resolve().parent != self.user_dir.resolve():
                raise UserError("只能卸载数据目录里的插件", status_code=403)
            old = folder.with_name(f".{folder.name}.old")
            _remove(old)
            with self._paused(plugin_id):
                try:
                    _move(folder, old)
                except OSError as error:
                    raise UserError(f"卸载失败（文件可能正被占用）：{error}") from None
                _remove(old)
            self.pending_restart.discard(plugin_id)
            self.reload()
            return info.id, info.name
