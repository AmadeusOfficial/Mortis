"""插件的加载结果和查询。重新加载时整份替换，正在跑的任务继续用它拿到的旧对象。

每个包（plugin.toml 加 `PLUGIN`）先归一化成一个 `Plugin`：包信息加具名扩展项
（来源、播放、钩子、数据类型）。旧的单对象简写（PLUGIN 直接是 SourcePlugin 或 PlayerPlugin）
和 DataTypePlugin 都在这里展开。注册表按 id 存条目：包本身一条，它带的来源和播放各一条
（parent 指向包），简写的单对象就只有一条。条目靠 `capabilities` 说明自己提供什么。
"""

import ast
import logging
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
from mortis.plugin_api import (
    DataTypePlugin,
    HookPlugin,
    PlayerPlugin,
    Plugin,
    SourcePlugin,
)
from mortis.plugin_api.events import event_model
from mortis.plugin_api.hooks import WRITE_PERMISSION
from mortis.plugins.hook_state import HookHealth, HookStatus
from mortis.plugins.loader import (
    Manifest,
    ManifestHook,
    PluginLoadError,
    check_imports,
    id_problem,
    import_plugin,
    read_manifest,
)
from mortis.settings import Settings

Origin = Literal["builtin", "user"]
Capability = Literal["data_type", "source", "player", "hook"]
CAPABILITIES: tuple[Capability, ...] = ("data_type", "source", "player", "hook")
_SORT_RANK: dict[str, int] = {"data_type": 0, "player": 1, "source": 2, "hook": 3}
"""条目排序：先按它提供的第一类能力，再按 id。没加载的简写插件看不出能力，排在最后。"""
USER_FOLDER = "plugins"
BUILTIN_IDS = frozenset(spec.id for spec in BUILTIN_TYPES)
_LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class PluginInfo:
    """注册表里的一条。包本身一条，包带的来源和播放各一条，简写的单对象也只有一条。

    plugin 是这一条自己的对象：来源或播放条目是那个对象；包条目是它的 DataTypePlugin
    （没有数据类型时为空，包的完整内容在 package 里）。capabilities 在没加载的条目上
    取自清单，简写的单对象还会粗读 `__init__.py` 里的类定义，只作显示用。
    """

    id: str
    name: str
    version: str
    folder: Path
    origin: Origin
    enabled: bool
    error: str | None = None
    plugin: DataTypePlugin | SourcePlugin | PlayerPlugin | None = None
    parent: str | None = None
    capabilities: tuple[Capability, ...] = ()
    data_type: str | None = None
    """来源条目服务的数据类型；没加载时取自清单。"""
    package: Plugin | None = None
    """只有包条目有：归一化后的全部扩展项。"""
    hooks: tuple[ManifestHook, ...] = ()
    """清单声明的钩子，运行时由事件钩子的工作者接上。"""

    @property
    def active(self) -> bool:
        return self.enabled and (self.plugin is not None or self.package is not None)

    @property
    def subscriptions(self) -> tuple[str, ...]:
        """订阅的事件名（去重，按声明顺序），设置页显示用。"""
        return tuple(dict.fromkeys(entry.event for entry in self.hooks))

    @property
    def hook_writes(self) -> tuple[str, ...]:
        """各钩子声明的写权限（去重），设置页显示用。"""
        return tuple(dict.fromkeys(w for entry in self.hooks for w in entry.writes))

    def provides(self, capability: Capability) -> bool:
        return capability in self.capabilities


@dataclass(frozen=True)
class Subscriber:
    """订阅了某个事件的钩子：所在包 id、钩子对象、清单里的声明（事件、数据类型、权限）。"""

    plugin_id: str
    hook: HookPlugin
    entry: ManifestHook


FILE_KINDS = frozenset({"video", "game"})


def _ordered(found: Iterable[str]) -> tuple[Capability, ...]:
    wanted = set(found)
    return tuple(cap for cap in CAPABILITIES if cap in wanted)


def _probe_capabilities(folder: Path) -> set[str]:
    """没 import 的包只能粗读 `__init__.py` 顶层：继承了哪个插件基类、有没有用 DataTypePlugin。

    只给设置页显示用，读不了就当没有。
    """
    found: set[str] = set()
    try:
        tree = ast.parse((folder / "__init__.py").read_text(encoding="utf-8-sig"))
    except OSError, UnicodeDecodeError, SyntaxError, ValueError:
        return found
    bases = {
        "SourcePlugin": "source",
        "PlayerPlugin": "player",
        "HookPlugin": "hook",
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for base in node.bases:
                name = base.id if isinstance(base, ast.Name) else getattr(base, "attr", "")
                if name in bases:
                    found.add(bases[name])
        elif isinstance(node, ast.Call):
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
            if name == "DataTypePlugin":
                found.add("data_type")
    return found


def _manifest_capabilities(manifest: Manifest) -> tuple[Capability, ...]:
    found = _probe_capabilities(manifest.folder)
    if manifest.sources:
        found.add("source")
    if manifest.players:
        found.add("player")
    if manifest.hooks:
        found.add("hook")
    return _ordered(found)


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


def _validate_source_object(plugin: object, label: str = "PLUGIN") -> SourcePlugin:
    if not isinstance(plugin, SourcePlugin):
        raise PluginLoadError(f"{label} 不是 SourcePlugin")
    for attribute in ("id", "name", "data_type", "provider"):
        if not isinstance(getattr(plugin, attribute, None), str) or not getattr(plugin, attribute):
            raise PluginLoadError(f"{label} 缺少 {attribute}")
    return plugin


def _validate_hook_object(plugin: object) -> HookPlugin:
    if not isinstance(plugin, HookPlugin):
        raise PluginLoadError("钩子不是 HookPlugin")
    for attribute in ("id", "name"):
        if not isinstance(getattr(plugin, attribute, None), str) or not getattr(plugin, attribute):
            raise PluginLoadError(f"钩子缺少 {attribute}")
    return plugin


def _tuple_of(value: object, label: str) -> tuple[object, ...]:
    if not isinstance(value, tuple):
        raise PluginLoadError(f"{label} 必须是元组")
    return value


def _normalize(plugin: object) -> tuple[Plugin, bool]:
    """把 PLUGIN 展开成 Plugin；第二项为真表示它是旧的单对象简写（来源或播放）。"""
    if isinstance(plugin, SourcePlugin):
        return Plugin(sources=(plugin,)), True
    if isinstance(plugin, PlayerPlugin):
        return Plugin(players=(plugin,)), True
    if isinstance(plugin, DataTypePlugin):
        return Plugin(data_type=plugin), False
    if isinstance(plugin, Plugin):
        return plugin, False
    raise PluginLoadError(
        "PLUGIN 不是插件对象（SourcePlugin、PlayerPlugin、DataTypePlugin 或 Plugin）"
    )


@dataclass(frozen=True)
class _Content:
    """校验过的包内容。sources、players 已把数据类型自带的并进来。"""

    sources: tuple[SourcePlugin, ...]
    players: tuple[PlayerPlugin, ...]
    hooks: tuple[HookPlugin, ...]
    data_type: DataTypePlugin | None
    shorthand: bool

    def package(self) -> Plugin:
        return Plugin(self.sources, self.players, self.hooks, self.data_type)


def _check_listed(table: str, listed: list[str], found: list[str]) -> None:
    """plugin.toml 里某张表的 id 和代码对象的 id 要逐项一致，多了少了都报错。"""
    if len(listed) != len(set(listed)):
        raise PluginLoadError(f"plugin.toml 的 {table} 里有重复 id")
    if set(listed) == set(found):
        return
    detail = []
    if set(found) - set(listed):
        detail.append(f"plugin.toml 少了 {'、'.join(sorted(set(found) - set(listed)))}")
    if set(listed) - set(found):
        detail.append(f"plugin.toml 多了 {'、'.join(sorted(set(listed) - set(found)))}")
    raise PluginLoadError(f"plugin.toml 的 {table} 名单和插件对象不一致：" + "；".join(detail))


def _validate_package(manifest: Manifest, package: Plugin) -> _Content:
    data_type = package.data_type
    taken = {manifest.id}
    if data_type is not None:
        if not isinstance(data_type, DataTypePlugin):
            raise PluginLoadError("Plugin.data_type 不是 DataTypePlugin")
        if data_type.spec.id in BUILTIN_IDS:
            raise PluginLoadError(f"数据类型 {data_type.spec.id} 和内置类型重名")
        errors = validate_data_type(data_type.spec)
        if errors:
            raise PluginLoadError("字段目录不合格：" + "；".join(errors))
        taken.add(data_type.spec.id)
    own_sources = _tuple_of(package.sources, "Plugin.sources")
    own_players = _tuple_of(package.players, "Plugin.players")
    hooks_raw = _tuple_of(package.hooks, "Plugin.hooks")
    sources_raw = (*(data_type.sources if data_type else ()), *own_sources)
    players_raw = (*(data_type.players if data_type else ()), *own_players)
    if data_type is not None:
        _tuple_of(data_type.players, "DataTypePlugin.players")
    declared = {entry.id: entry for entry in manifest.sources}

    sources: list[SourcePlugin] = []
    for raw in sources_raw:
        source = _validate_source_object(raw)
        problem = id_problem(source.id)
        if problem:
            raise PluginLoadError(f"子来源 id「{source.id}」{problem}")
        if (
            data_type is not None
            and raw in data_type.sources
            and source.data_type != data_type.spec.id
        ):
            raise PluginLoadError(
                f"子来源 {source.id} 的 data_type 是 {source.data_type}，"
                f"应等于数据类型 {data_type.spec.id}"
            )
        entry = declared.get(source.id)
        if entry is not None and entry.data_type and entry.data_type != source.data_type:
            raise PluginLoadError(
                f"plugin.toml 里来源 {source.id} 的 data_type 是 {entry.data_type}，"
                f"代码里是 {source.data_type}"
            )
        if source.id in taken:
            raise PluginLoadError(
                f"子来源 {source.id} 不能和数据类型同名"
                if data_type is not None and source.id == data_type.spec.id
                else f"子来源 {source.id} 重名"
            )
        taken.add(source.id)
        sources.append(source)
    players: list[PlayerPlugin] = []
    for raw in players_raw:
        player = _validate_player_object(raw, "子播放插件")
        problem = id_problem(player.id)
        if problem:
            raise PluginLoadError(f"子播放插件 id「{player.id}」{problem}")
        if player.id in taken:
            raise PluginLoadError(f"子播放插件 {player.id} 重名")
        taken.add(player.id)
        players.append(player)
    hooks: list[HookPlugin] = []
    for raw in hooks_raw:
        hook = _validate_hook_object(raw)
        problem = id_problem(hook.id)
        if problem:
            raise PluginLoadError(f"钩子 id「{hook.id}」{problem}")
        if hook.id in taken:
            raise PluginLoadError(f"钩子 {hook.id} 重名")
        taken.add(hook.id)
        hooks.append(hook)

    _check_listed("sources", [e.id for e in manifest.sources], [s.id for s in sources])
    _check_listed("players", [e.id for e in manifest.players], [p.id for p in players])
    _check_listed("hooks", [e.id for e in manifest.hooks], [h.id for h in hooks])
    for hook_entry in manifest.hooks:
        if event_model(hook_entry.event) is None:
            raise PluginLoadError(f"钩子 {hook_entry.id} 订阅了不存在的事件「{hook_entry.event}」")
        for permission in hook_entry.writes:
            if not WRITE_PERMISSION.fullmatch(permission):
                raise PluginLoadError(
                    f"钩子 {hook_entry.id} 声明了不认识的权限「{permission}」，"
                    "现在只支持 tags:<标签类别>"
                )
    return _Content(tuple(sources), tuple(players), tuple(hooks), data_type, False)


def _validate_shorthand(manifest: Manifest, package: Plugin) -> _Content:
    """单个对象的简写：对象的 id 就是包 id，plugin.toml 里不再列子项。"""
    for table, listed in (
        ("sources", manifest.sources),
        ("players", manifest.players),
        ("hooks", manifest.hooks),
    ):
        if listed:
            raise PluginLoadError(
                f"PLUGIN 是单个对象时，plugin.toml 不能再写 [[{table}]]；"
                "要带多样东西请用 Plugin(...) 或 DataTypePlugin(...)"
            )
    if package.sources:
        source = _validate_source_object(package.sources[0])
        content = _Content((source,), (), (), None, True)
        object_id = source.id
    else:
        player = _validate_player_object(package.players[0])
        content = _Content((), (player,), (), None, True)
        object_id = player.id
    if object_id != manifest.id:
        raise PluginLoadError(f"PLUGIN.id 是 {object_id}，和 plugin.toml 里的 {manifest.id} 不一致")
    return content


def _validate(manifest: Manifest, plugin: object) -> _Content:
    package, shorthand = _normalize(plugin)
    if shorthand:
        return _validate_shorthand(manifest, package)
    return _validate_package(manifest, package)


def _child_infos(
    manifest: Manifest, origin: Origin, enabled: bool, content: _Content, disabled: set[str]
) -> list[PluginInfo]:
    """包带的来源和播放，各自可以单独停用；包停用时它们都不启用。"""
    infos: list[PluginInfo] = []
    children: list[tuple[Capability, SourcePlugin | PlayerPlugin]] = [
        *(("source", source) for source in content.sources),
        *(("player", player) for player in content.players),
    ]
    for capability, child in children:
        on = enabled and child.id not in disabled
        infos.append(
            PluginInfo(
                child.id,
                child.name,
                manifest.version,
                manifest.folder,
                origin,
                on,
                plugin=child if on else None,
                parent=manifest.id,
                capabilities=(capability,),
                data_type=child.data_type if isinstance(child, SourcePlugin) else None,
            )
        )
    return infos


def _unloaded_children(manifest: Manifest, origin: Origin, error: str | None) -> list[PluginInfo]:
    """包停用或导入失败时不 import，用 plugin.toml 的名单把子来源和子播放列成停用项。

    包导入失败时，它们带上包的错误并保持停用，不显示为启用。
    """
    children: list[tuple[Capability, str, str, str | None]] = [
        *(("source", e.id, e.name, e.data_type or None) for e in manifest.sources),
        *(("player", e.id, e.name, None) for e in manifest.players),
    ]
    return [
        PluginInfo(
            child_id,
            name,
            manifest.version,
            manifest.folder,
            origin,
            False,
            error=error,
            parent=manifest.id,
            capabilities=(capability,),
            data_type=data_type,
        )
        for capability, child_id, name, data_type in children
    ]


def _dupe_message_for(manifest: Manifest, clash_id: str, owner: str) -> str:
    if clash_id == manifest.id:
        return f"id {clash_id} 和插件 {owner} 重名，整包加载失败"
    if any(entry.id == clash_id for entry in manifest.players):
        label = "子播放插件"
    elif any(entry.id == clash_id for entry in manifest.hooks):
        label = "钩子"
    else:
        label = "子来源"
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


def _manifest_ids(manifest: Manifest) -> list[str]:
    """这个包会占用的全部 id（包 id 加三张子表的 id），仅凭 manifest，不 import。"""
    return [
        manifest.id,
        *(entry.id for entry in manifest.sources),
        *(entry.id for entry in manifest.players),
        *(entry.id for entry in manifest.hooks),
    ]


def _build_infos(manifest: Manifest, origin: Origin, disabled: set[str]) -> list[PluginInfo]:
    """确认可放置后才调用：这里才 check_imports / import_plugin，拒绝的包不会走到这。"""
    enabled = manifest.id not in disabled
    base = {
        "id": manifest.id,
        "name": manifest.name,
        "version": manifest.version,
        "folder": manifest.folder,
        "origin": origin,
        "hooks": manifest.hooks,
    }
    guess = _manifest_capabilities(manifest)
    if not enabled:
        return [
            PluginInfo(**base, enabled=False, capabilities=guess),
            *_unloaded_children(manifest, origin, None),
        ]
    try:
        check_imports(manifest.folder)
        module = import_plugin(manifest)
        content = _validate(manifest, getattr(module, "PLUGIN", None))
    except PluginLoadError as error:
        return [
            PluginInfo(**base, enabled=True, error=str(error), capabilities=guess),
            *_unloaded_children(manifest, origin, f"父插件 {manifest.id} 加载失败：{error}"),
        ]
    package = content.package()
    capabilities = _ordered(
        [
            *(["data_type"] if content.data_type else []),
            *(["source"] if content.sources else []),
            *(["player"] if content.players else []),
            *(["hook"] if content.hooks else []),
        ]
    )
    if content.shorthand:
        own: SourcePlugin | PlayerPlugin = (
            content.sources[0] if content.sources else content.players[0]
        )
        return [
            PluginInfo(
                **base,
                enabled=True,
                plugin=own,
                capabilities=capabilities,
                data_type=own.data_type if isinstance(own, SourcePlugin) else None,
                package=package,
            )
        ]
    return [
        PluginInfo(
            **base,
            enabled=True,
            plugin=content.data_type,
            capabilities=capabilities,
            package=package,
        ),
        *_child_infos(manifest, origin, True, content, disabled),
    ]


def _sort_key(info: PluginInfo) -> tuple[int, str]:
    rank = min((_SORT_RANK[cap] for cap in info.capabilities), default=len(_SORT_RANK))
    return rank, info.id


class PluginRegistry:
    def __init__(self, folders: Iterable[tuple[Path, Origin]] = ()) -> None:
        self.folders = list(folders)
        self._guard = threading.Lock()
        self._plugins: dict[str, PluginInfo] = {}
        self._off: set[str] = set()
        self.health = HookHealth()

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
            self._off = set(off)
        # 熔断后写进了 disabled；用户把它从名单里去掉再重新加载，就是重新启用。
        for hook_id in self.health.tripped_ids():
            if hook_id not in off:
                self.health.reset(hook_id)

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
            bad = PluginInfo(folder.name, folder.name, "", folder, origin, False, str(error))
            _place(
                packages, owners, folder.name, [folder.name], lambda: [bad], lambda *_: bad, folder
            )
            return

        def error_entry(clash_id: str, owner: str) -> PluginInfo:
            return PluginInfo(
                manifest.id,
                manifest.name,
                manifest.version,
                folder,
                origin,
                False,
                error=_dupe_message_for(manifest, clash_id, owner),
                capabilities=_manifest_capabilities(manifest),
                hooks=manifest.hooks,
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
            return sorted(self._plugins.values(), key=_sort_key)

    def hook_disabled(self, hook_id: str) -> bool:
        """钩子单独被停用：设置文件的 `[plugins] disabled` 里有它，或者运行中熔断了。"""
        return hook_id in self._off or self.health.tripped(hook_id)

    def hooks(self) -> list[tuple[str, HookPlugin]]:
        """启用的包带的、没被单独停用的钩子，每项是 (包 id, 钩子对象)。事件运行时从这里取。"""
        return [
            (info.id, hook)
            for info in self.all()
            if info.active and info.package is not None
            for hook in info.package.hooks
            if not self.hook_disabled(hook.id)
        ]

    def hook_status(self, hook_id: str) -> HookStatus:
        """设置页用：这个钩子是否停用、为什么、连续失败几次。"""
        return self.health.status(hook_id, off=hook_id in self._off)

    def subscribers(self, event_type: str, data_type: str) -> list[Subscriber]:
        """订阅了这个事件、并且数据类型对得上的钩子。

        钩子清单里 data_types 为空表示不限；写了名单时，事件没有数据类型也不投递。
        """
        found: list[Subscriber] = []
        for info in self.all():
            if not info.active or info.package is None:
                continue
            declared = {entry.id: entry for entry in info.hooks}
            for hook in info.package.hooks:
                entry = declared.get(hook.id)
                if entry is None or entry.event != event_type or self.hook_disabled(hook.id):
                    continue
                if entry.data_types and data_type not in entry.data_types:
                    continue
                found.append(Subscriber(info.id, hook, entry))
        return found

    def disable_hook(self, hook_id: str) -> None:
        """进程内立即停用；写进设置文件由调用方做。"""
        self._off.add(hook_id)

    def enable_hook(self, hook_id: str) -> None:
        self._off.discard(hook_id)
        self.health.reset(hook_id)

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
