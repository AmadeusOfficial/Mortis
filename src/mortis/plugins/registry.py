"""插件的加载结果和查询。重新加载时整份替换，正在跑的任务继续用它拿到的旧对象。"""

import threading
from collections.abc import Iterable
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
    Kind,
    Manifest,
    PluginLoadError,
    check_imports,
    import_plugin,
    read_manifest,
)
from mortis.settings import Settings

Origin = Literal["builtin", "user"]
USER_FOLDER = "plugins"
BUILTIN_IDS = frozenset(spec.id for spec in BUILTIN_TYPES)


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

    @property
    def active(self) -> bool:
        return self.enabled and self.plugin is not None


def _validate_player(manifest: Manifest, plugin: object) -> PlayerPlugin:
    if not isinstance(plugin, PlayerPlugin):
        raise PluginLoadError("PLUGIN 不是 PlayerPlugin")
    for attribute in ("id", "name"):
        if not isinstance(getattr(plugin, attribute, None), str) or not getattr(plugin, attribute):
            raise PluginLoadError(f"PLUGIN 缺少 {attribute}")
    if plugin.id != manifest.id:
        raise PluginLoadError(f"PLUGIN.id 是 {plugin.id}，和 plugin.toml 里的 {manifest.id} 不一致")
    return plugin


def _validate(manifest: Manifest, plugin: object) -> DataTypePlugin | SourcePlugin | PlayerPlugin:
    if manifest.kind == "player":
        return _validate_player(manifest, plugin)
    if manifest.kind == "data_type":
        if not isinstance(plugin, DataTypePlugin):
            raise PluginLoadError("PLUGIN 不是 DataTypePlugin")
        if plugin.spec.id in BUILTIN_IDS:
            raise PluginLoadError(f"数据类型 {plugin.spec.id} 和内置类型重名")
        errors = validate_data_type(plugin.spec)
        if errors:
            raise PluginLoadError("字段目录不合格：" + "；".join(errors))
        return plugin
    if not isinstance(plugin, SourcePlugin):
        raise PluginLoadError("PLUGIN 不是 SourcePlugin")
    for attribute in ("id", "name", "data_type", "provider"):
        if not isinstance(getattr(plugin, attribute, None), str) or not getattr(plugin, attribute):
            raise PluginLoadError(f"PLUGIN 缺少 {attribute}")
    if plugin.id != manifest.id:
        raise PluginLoadError(f"PLUGIN.id 是 {plugin.id}，和 plugin.toml 里的 {manifest.id} 不一致")
    return plugin


def _load_one(folder: Path, origin: Origin, disabled: set[str]) -> PluginInfo | None:
    try:
        manifest = read_manifest(folder)
    except PluginLoadError as error:
        return PluginInfo(folder.name, folder.name, "", "source", folder, origin, False, str(error))
    enabled = manifest.id not in disabled
    base = (manifest.id, manifest.name, manifest.version, manifest.kind, folder, origin, enabled)
    if not enabled:
        return PluginInfo(*base)
    try:
        check_imports(folder)
        module = import_plugin(manifest)
        plugin = _validate(manifest, getattr(module, "PLUGIN", None))
    except PluginLoadError as error:
        return PluginInfo(*base, error=str(error))
    return PluginInfo(*base, plugin=plugin)


class PluginRegistry:
    def __init__(self, folders: Iterable[tuple[Path, Origin]] = ()) -> None:
        self.folders = list(folders)
        self._guard = threading.Lock()
        self._plugins: dict[str, PluginInfo] = {}

    def load(self, disabled: Iterable[str] = ()) -> None:
        """后面的目录覆盖前面同 id 的插件。"""
        off = set(disabled)
        found: dict[str, PluginInfo] = {}
        for root, origin in self.folders:
            if not root.is_dir():
                continue
            for folder in sorted(path for path in root.iterdir() if path.is_dir()):
                if folder.name.startswith((".", "_")):
                    continue
                info = _load_one(folder, origin, off)
                if info is not None:
                    found[info.id] = info
        with self._guard:
            self._plugins = found

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
