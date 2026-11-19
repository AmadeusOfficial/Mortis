"""插件的加载结果和查询。重新加载时整份替换，正在跑的任务继续用它拿到的旧对象。"""

import logging
import re
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from mortis.catalog.install import BUILTIN_TYPES, InvalidDataType, install_data_type
from mortis.catalog.spec import validate_data_type
from mortis.db.models import DataType, SourceOrder
from mortis.plugin_api import DataTypePlugin, PlayerPlugin, SourcePlugin
from mortis.plugins.loader import (
    Manifest,
    ManifestSource,
    PluginLoadError,
    check_imports,
    import_plugin,
    read_manifest,
)
from mortis.settings import Settings

Origin = Literal["builtin", "user"]
Kind = Literal["data_type", "source", "player"]
USER_FOLDER = "plugins"
BUILTIN_IDS = frozenset(spec.id for spec in BUILTIN_TYPES)
_LOG = logging.getLogger(__name__)


_ID = re.compile(r"^[a-z][a-z0-9_]*$")


@dataclass(frozen=True)
class PluginInfo:
    id: str
    name: str
    version: str
    kind: Kind
    folder: Path
    origin: Origin
    enabled: bool
    error: str | None = None
    plugin: DataTypePlugin | SourcePlugin | PlayerPlugin | None = None
    parent: str | None = None

    @property
    def active(self) -> bool:
        return self.enabled and self.plugin is not None


FILE_KINDS = frozenset({"video", "game"})


def _validate_player_object(plugin: object, label: str = "PLUGIN") -> PlayerPlugin:
    if not isinstance(plugin, PlayerPlugin):
        raise PluginLoadError(f"{label} 不是 PlayerPlugin")
    for attribute in ("id", "name"):
        if not isinstance(getattr(plugin, attribute, None), str) or not getattr(plugin, attribute):
            raise PluginLoadError(f"{label} 缺少 {attribute}")
    kinds = plugin.file_kinds
    if not isinstance(kinds, tuple) or not kinds or any(kind not in FILE_KINDS for kind in kinds):
        raise PluginLoadError(f"{plugin.id} 的 file_kinds 只能是 video、game 组成的元组")
    return plugin


def _validate_player(manifest: Manifest, plugin: object) -> PlayerPlugin:
    plugin = _validate_player_object(plugin)
    if plugin.id != manifest.id:
        raise PluginLoadError(f"PLUGIN.id 是 {plugin.id}，和 plugin.toml 里的 {manifest.id} 不一致")
    return plugin


def _validate_source_object(plugin: object) -> SourcePlugin:
    if not isinstance(plugin, SourcePlugin):
        raise PluginLoadError("PLUGIN 不是 SourcePlugin")
    for attribute in ("id", "name", "data_type", "provider"):
        if not isinstance(getattr(plugin, attribute, None), str) or not getattr(plugin, attribute):
            raise PluginLoadError(f"PLUGIN 缺少 {attribute}")
    return plugin


def _validate_bundled_sources(manifest: Manifest, plugin: DataTypePlugin) -> None:
    """数据类型自带的来源：必须是 SourcePlugin、data_type 等于本类型、id 合法且本插件内不重名；
    对象的 id 列表还要和 plugin.toml 的 [[sources]] 名单完全一致（集合相同、无重复）。"""
    seen: set[str] = set()
    for source in plugin.sources:
        source = _validate_source_object(source)
        if not _ID.fullmatch(source.id):
            raise PluginLoadError(f"子来源 id「{source.id}」只能用小写字母、数字和下划线")
        if source.data_type != plugin.spec.id:
            raise PluginLoadError(
                f"子来源 {source.id} 的 data_type 是 {source.data_type}，"
                f"应等于数据类型 {plugin.spec.id}"
            )
        if source.id == plugin.spec.id:
            raise PluginLoadError(f"子来源 {source.id} 不能和数据类型同名")
        if source.id in seen:
            raise PluginLoadError(f"子来源 {source.id} 重名")
        seen.add(source.id)
    listed = [entry.id for entry in manifest.sources]
    if len(listed) != len(set(listed)):
        raise PluginLoadError("plugin.toml 的 sources 里有重复 id")
    if set(listed) != seen:
        missing = seen - set(listed)
        extra = set(listed) - seen
        detail = []
        if missing:
            detail.append(f"plugin.toml 少了 {'、'.join(sorted(missing))}")
        if extra:
            detail.append(f"plugin.toml 多了 {'、'.join(sorted(extra))}")
        raise PluginLoadError(
            "plugin.toml 的 sources 名单和 DataTypePlugin.sources 不一致：" + "；".join(detail)
        )


def _validate_bundled_players(manifest: Manifest, plugin: DataTypePlugin) -> None:
    """数据类型自带的播放插件：必须是 PlayerPlugin、id 合法、包内不重名，
    对象的 id 列表和 plugin.toml 的 [[players]] 名单完全一致。"""
    if not isinstance(plugin.players, tuple):
        raise PluginLoadError("DataTypePlugin.players 必须是元组")
    taken = {plugin.spec.id, manifest.id, *(source.id for source in plugin.sources)}
    seen: set[str] = set()
    for player in plugin.players:
        player = _validate_player_object(player, "子播放插件")
        if not _ID.fullmatch(player.id):
            raise PluginLoadError(f"子播放插件 id「{player.id}」只能用小写字母、数字和下划线")
        if player.id in taken or player.id in seen:
            raise PluginLoadError(f"子播放插件 {player.id} 重名")
        seen.add(player.id)
    listed = {entry.id for entry in manifest.players}
    if listed != seen:
        detail = []
        if seen - listed:
            detail.append(f"plugin.toml 少了 {'、'.join(sorted(seen - listed))}")
        if listed - seen:
            detail.append(f"plugin.toml 多了 {'、'.join(sorted(listed - seen))}")
        raise PluginLoadError(
            "plugin.toml 的 players 名单和 DataTypePlugin.players 不一致：" + "；".join(detail)
        )


def _guess_kind(plugin: object) -> Kind:
    if isinstance(plugin, DataTypePlugin):
        return "data_type"
    return "player" if isinstance(plugin, PlayerPlugin) else "source"


def _validate(manifest: Manifest, plugin: object) -> DataTypePlugin | SourcePlugin | PlayerPlugin:
    kind = (
        manifest.kind if manifest.kind in ("data_type", "source", "player") else _guess_kind(plugin)
    )
    if kind == "player":
        return _validate_player(manifest, plugin)
    if kind == "data_type":
        if not isinstance(plugin, DataTypePlugin):
            raise PluginLoadError("PLUGIN 不是 DataTypePlugin")
        if plugin.spec.id in BUILTIN_IDS:
            raise PluginLoadError(f"数据类型 {plugin.spec.id} 和内置类型重名")
        errors = validate_data_type(plugin.spec)
        if errors:
            raise PluginLoadError("字段目录不合格：" + "；".join(errors))
        _validate_bundled_sources(manifest, plugin)
        _validate_bundled_players(manifest, plugin)
        return plugin
    source = _validate_source_object(plugin)
    if source.id != manifest.id:
        raise PluginLoadError(f"PLUGIN.id 是 {source.id}，和 plugin.toml 里的 {manifest.id} 不一致")
    return source


def _bundled_source_infos(
    manifest: Manifest, origin: Origin, error: str | None
) -> list[PluginInfo]:
    """父插件停用或导入失败时不 import，用 plugin.toml 的 [[sources]]、[[players]] 名单
    把子来源和子播放插件列成停用项。

    父插件导入失败时，它们带上父插件的错误并保持停用，不显示为启用。
    """
    children: list[tuple[Kind, ManifestSource]] = [
        *(("source", entry) for entry in manifest.sources),
        *(("player", entry) for entry in manifest.players),
    ]
    return [
        PluginInfo(
            entry.id,
            entry.name,
            manifest.version,
            kind,
            manifest.folder,
            origin,
            False,
            error=error,
            parent=manifest.id,
        )
        for kind, entry in children
    ]


def _active_source_infos(
    manifest: Manifest, plugin: DataTypePlugin, origin: Origin, disabled: set[str]
) -> list[PluginInfo]:
    """父插件启用并导入成功时，从 DataTypePlugin.sources、players 取对象，各自可单独停用。"""
    children: list[tuple[Kind, SourcePlugin | PlayerPlugin]] = [
        *(("source", source) for source in plugin.sources),
        *(("player", player) for player in plugin.players),
    ]
    infos: list[PluginInfo] = []
    for kind, child in children:
        enabled = child.id not in disabled
        infos.append(
            PluginInfo(
                child.id,
                child.name,
                manifest.version,
                kind,
                manifest.folder,
                origin,
                enabled,
                plugin=child if enabled else None,
                parent=manifest.id,
            )
        )
    return infos


def _dupe_message_for(manifest: Manifest, clash_id: str, owner: str) -> str:
    if clash_id == manifest.id:
        return f"id {clash_id} 和插件 {owner} 重名，整包加载失败"
    label = "子播放插件" if any(entry.id == clash_id for entry in manifest.players) else "子来源"
    return f"{label} {clash_id} 和插件 {owner} 重名，整包加载失败"


def _place(
    packages: dict[str, list[PluginInfo]],
    owners: dict[str, str],
    primary: str,
    ids: list[str],
    build: Callable[[], list[PluginInfo]],
    error_entry: Callable[[str, str], PluginInfo],
    folder: Path,
) -> None:
    """用 manifest 推出的 ids 先做冲突预检，确认可放置后才 build()（这一步才 import）。

    忽略「将被同 primary id 替换的旧包」自己的成员。冲突时不 build、不 import：
    primary id 还空着就列一条错误条目；已被占用就保留旧包、记 warning。
    """
    collision = next(
        ((cid, owners[cid]) for cid in ids if cid in owners and owners[cid] != primary),
        None,
    )
    if collision is None:
        # 确认可放置：先移除同 primary id 的旧包，再 import 并整包放入；包内所有 id 都归 primary。
        for old in packages.pop(primary, []):
            owners.pop(old.id, None)
        infos = build()
        for info in infos:
            owners[info.id] = primary
        packages[primary] = infos
        return
    clash_id, owner = collision
    if primary in owners:
        # primary id 已被别的包占用（如 user 覆盖包又和第三方冲突）：保留旧包、不 import、记日志。
        _LOG.warning(
            "插件目录 %s 的 %s 和插件 %s 的 id %s 重名，已跳过", folder, primary, owner, clash_id
        )
        return
    # primary id 还空着：列一条带 error 的顶层条目（不列子来源），也不 import。
    owners[primary] = primary
    packages[primary] = [error_entry(clash_id, owner)]


def _manifest_kind(manifest: Manifest) -> Kind:
    if manifest.kind in ("data_type", "source", "player"):
        return manifest.kind  # type: ignore[return-value]
    return "data_type" if manifest.sources or manifest.players else "source"


def _manifest_ids(manifest: Manifest) -> list[str]:
    """这个包会占用的全部 id：父 id 加 [[sources]]、[[players]] 的 id，仅凭 manifest，不 import。"""
    return [
        manifest.id,
        *(entry.id for entry in manifest.sources),
        *(entry.id for entry in manifest.players),
    ]


def _build_infos(manifest: Manifest, origin: Origin, disabled: set[str]) -> list[PluginInfo]:
    """确认可放置后才调用：这里才 check_imports / import_plugin，拒绝的包不会走到这。"""
    enabled = manifest.id not in disabled
    base = (
        manifest.id,
        manifest.name,
        manifest.version,
        _manifest_kind(manifest),
        manifest.folder,
        origin,
        enabled,
    )
    if not enabled:
        return [
            PluginInfo(*base),
            *_bundled_source_infos(manifest, origin, None),
        ]
    try:
        check_imports(manifest.folder)
        module = import_plugin(manifest)
        plugin = _validate(manifest, getattr(module, "PLUGIN", None))
    except PluginLoadError as error:
        return [
            PluginInfo(*base, error=str(error)),
            *_bundled_source_infos(manifest, origin, f"父插件 {manifest.id} 加载失败：{error}"),
        ]
    infos = [PluginInfo(*base, plugin=plugin)]
    if isinstance(plugin, DataTypePlugin):
        infos.extend(_active_source_infos(manifest, plugin, origin, disabled))
    return infos


class PluginRegistry:
    def __init__(self, folders: Iterable[tuple[Path, Origin]] = ()) -> None:
        self.folders = list(folders)
        self._guard = threading.Lock()
        self._plugins: dict[str, PluginInfo] = {}

    def load(self, disabled: Iterable[str] = ()) -> None:
        """后面的目录按整个插件包覆盖同 primary id 的插件（父 + 全部子来源一起换）。

        先只读 manifest 拿到这个包会占用的 id（父 id 加 [[sources]] 名单），用它们做冲突预检；
        忽略「将被替换的旧包」自己的成员。确认可放置后才 import 新包——冲突被拒的包绝不 import，
        不会清掉被保留旧包的 sys.modules。注册表里任何时候 id 唯一。
        """
        off = set(disabled)
        # packages 按 primary id 存可被覆盖的包；owners 记每个 id 当前属于哪个 primary id。
        packages: dict[str, list[PluginInfo]] = {}
        owners: dict[str, str] = {}
        for root, origin in self.folders:
            if not root.is_dir():
                continue
            for folder in sorted(path for path in root.iterdir() if path.is_dir()):
                if folder.name.startswith((".", "_")):
                    continue
                self._place_folder(packages, owners, folder, origin, off)
        with self._guard:
            self._plugins = {info.id: info for infos in packages.values() for info in infos}

    @staticmethod
    def _place_folder(
        packages: dict[str, list[PluginInfo]],
        owners: dict[str, str],
        folder: Path,
        origin: Origin,
        disabled: set[str],
    ) -> None:
        try:
            manifest = read_manifest(folder)
        except PluginLoadError as error:
            # manifest 读不了：这个包只有一个 id（文件夹名），不 import，照常参与冲突预检。
            bad = PluginInfo(
                folder.name, folder.name, "", "source", folder, origin, False, str(error)
            )
            _place(
                packages, owners, folder.name, [folder.name], lambda: [bad], lambda *_: bad, folder
            )
            return

        def error_entry(clash_id: str, owner: str) -> PluginInfo:
            return PluginInfo(
                manifest.id,
                manifest.name,
                manifest.version,
                _manifest_kind(manifest),
                folder,
                origin,
                False,
                error=_dupe_message_for(manifest, clash_id, owner),
            )

        _place(
            packages,
            owners,
            manifest.id,
            _manifest_ids(manifest),
            lambda: _build_infos(manifest, origin, disabled),
            error_entry,
            folder,
        )

    def all(self) -> list[PluginInfo]:
        with self._guard:
            return sorted(self._plugins.values(), key=lambda info: (info.kind, info.id))

    def get(self, plugin_id: str) -> PluginInfo | None:
        with self._guard:
            return self._plugins.get(plugin_id)

    def data_types(self) -> list[tuple[str, DataTypePlugin]]:
        return [
            (info.id, info.plugin)
            for info in self.all()
            if info.active and isinstance(info.plugin, DataTypePlugin)
        ]

    def sources(self, data_type_id: str) -> list[SourcePlugin]:
        return [
            info.plugin
            for info in self.all()
            if info.active
            and isinstance(info.plugin, SourcePlugin)
            and info.plugin.data_type == data_type_id
        ]

    def source(self, source_id: str) -> SourcePlugin | None:
        info = self.get(source_id)
        if info is not None and info.active and isinstance(info.plugin, SourcePlugin):
            return info.plugin
        return None

    def players(self) -> list[PlayerPlugin]:
        return [
            info.plugin
            for info in self.all()
            if info.active and isinstance(info.plugin, PlayerPlugin)
        ]

    def player(self, player_id: str) -> PlayerPlugin | None:
        info = self.get(player_id)
        if info is not None and info.active and isinstance(info.plugin, PlayerPlugin):
            return info.plugin
        return None

    def sync_catalog(self, session: Session) -> None:
        """装上启用的数据类型；没装上的插件类型停用，行和条目保留。"""
        installed: set[str] = set()
        for plugin_id, plugin in self.data_types():
            try:
                install_data_type(session, plugin.spec, plugin_id=plugin_id)
            except InvalidDataType:
                continue
            row = session.get(DataType, plugin.spec.id)
            if row is not None:
                row.is_enabled = 1
            seed_orders(session, plugin.spec.id, plugin.default_orders)
            installed.add(plugin.spec.id)
        for row in session.scalars(select(DataType).where(DataType.plugin_id.is_not(None))):
            if row.id not in installed:
                row.is_enabled = 0
        session.flush()


def plugin_folders(settings: Settings) -> list[tuple[Path, Origin]]:
    folders: list[tuple[Path, Origin]] = []
    if settings.plugins_dir is not None:
        folders.append((settings.plugins_dir, "builtin"))
    folders.append((settings.data_dir / USER_FOLDER, "user"))
    return folders


def seed_orders(session: Session, data_type_id: str, orders: dict[str, list[str]]) -> None:
    """只补还没有行的字段。字段行没列出的整体来源记为排除，表示这个字段只用列出的来源。"""
    existing = set(
        session.scalars(
            select(SourceOrder.field_key).where(SourceOrder.data_type_id == data_type_id)
        )
    )
    whole = orders.get("", [])
    for field_key, sources in orders.items():
        if field_key in existing:
            continue
        excluded = [] if field_key == "" else [s for s in whole if s not in sources]
        for position, source in enumerate([*sources, *excluded]):
            session.add(
                SourceOrder(
                    data_type_id=data_type_id,
                    field_key=field_key,
                    source=source,
                    position=position,
                    is_excluded=int(source in excluded),
                )
            )
    session.flush()
