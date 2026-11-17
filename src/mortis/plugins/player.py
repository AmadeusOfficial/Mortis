"""播放插件拿到的宿主，以及按设置选出要用的播放插件。"""

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from mortis.config.models import AppConfig
from mortis.config.store import ConfigStore
from mortis.errors import UserError
from mortis.plugin_api import PlayerError, PlayerPlugin
from mortis.plugins.host import EXECUTABLES, plugin_settings
from mortis.plugins.launch import Launcher
from mortis.plugins.registry import PluginRegistry

logger = logging.getLogger("mortis.play")
SWITCH_HINT = "，在设置的「播放」里换一个"


def _default_log(message: str) -> None:
    logger.info(message)


class PluginPlayerHost:
    def __init__(
        self,
        plugin: PlayerPlugin,
        launcher: Launcher,
        config: AppConfig,
        log: Callable[[str], None] = _default_log,
    ) -> None:
        self.plugin = plugin
        self.settings = plugin_settings(config, plugin)
        self._launcher = launcher
        self._log = log

    def log(self, message: str) -> None:
        self._log(f"[{self.plugin.name}] {message}")

    def launch(self, executable: str, args: list[str], cwd: str | None = None) -> None:
        if not isinstance(executable, str) or not executable.strip():
            raise PlayerError("没有要启动的程序")
        if not isinstance(args, list) or not all(isinstance(arg, str) for arg in args):
            raise PlayerError("启动参数只能是字符串列表")
        if cwd is not None and (not isinstance(cwd, str) or not cwd.strip()):
            raise PlayerError("工作目录只能是一个文件夹路径")
        self.log(f"启动 {executable} {args}" + (f"，工作目录 {cwd}" if cwd else ""))
        try:
            self._launcher.launch(executable, list(args), cwd)
        except OSError as error:
            name = Path(executable).name
            raise PlayerError(f"启动 {name} 失败：{error.strerror or error}") from None

    def open_default(self, path: str) -> None:
        if not isinstance(path, str) or not path.strip():
            raise PlayerError("没有要打开的文件")
        self.log(f"用默认程序打开 {path}")
        try:
            self._launcher.open_default(path)
        except OSError as error:
            raise PlayerError(f"用默认程序打开失败：{error.strerror or error}") from None


def player_host(plugin: PlayerPlugin, launcher: Launcher, store: ConfigStore) -> PluginPlayerHost:
    return PluginPlayerHost(plugin, launcher, store.load_config())


def unavailable_reason(plugin: PlayerPlugin, host: PluginPlayerHost) -> str | None:
    """插件自己判断时出错也算不可用，原因给用户看。"""
    try:
        if plugin.available(host):
            return None
        return plugin.unavailable_reason(host) or "不可用"
    except Exception as error:
        return f"检查出错：{type(error).__name__}: {error}"


def opens(plugin: PlayerPlugin, file_kind: str) -> bool:
    kinds = getattr(plugin, "file_kinds", ())
    return isinstance(kinds, tuple) and file_kind in kinds


def video_player_problem(registry: PluginRegistry, player_id: str) -> tuple[str, bool] | None:
    """设置里选的视频播放插件为什么用不了；第二项为真表示该换一个插件，而不是去启用它。"""
    plugin = registry.player(player_id)
    if plugin is None:
        info = registry.get(player_id)
        if info is None or info.kind != "player":
            return f"没有播放插件「{player_id}」", True
        if not info.enabled:
            return f"播放插件「{info.name}」已停用", False
        return f"播放插件「{info.name}」没有加载：{info.error}", False
    if not opens(plugin, "video"):
        return f"播放插件「{plugin.name}」不能播放视频", True
    return None


def choose_player(
    registry: PluginRegistry, launcher: Launcher, store: ConfigStore
) -> tuple[PlayerPlugin, PluginPlayerHost]:
    """播放视频用的插件。设置里选了就用它；没选时用第一个可用的。选了但用不了时报原因，不换别的。"""
    chosen = store.load_config().playback.player
    if chosen:
        problem = video_player_problem(registry, chosen)
        plugin = registry.player(chosen)
        if problem is not None or plugin is None:
            message, switch = problem or (f"没有播放插件「{chosen}」", True)
            raise UserError(message + (SWITCH_HINT if switch else ""), status_code=503)
        host = player_host(plugin, launcher, store)
        reason = unavailable_reason(plugin, host)
        if reason is not None:
            raise UserError(f"{plugin.name} 不可用：{reason}", status_code=503)
        return plugin, host
    for plugin in registry.players():
        if not opens(plugin, "video"):
            continue
        host = player_host(plugin, launcher, store)
        if unavailable_reason(plugin, host) is None:
            return plugin, host
    raise UserError("没有可用的播放插件", status_code=503)


def choose_game_player(
    registry: PluginRegistry, launcher: Launcher, store: ConfigStore
) -> tuple[PlayerPlugin, PluginPlayerHost]:
    """启动游戏用第一个可用的 game 插件；都用不了时报第一个的原因。"""
    first: tuple[PlayerPlugin, str] | None = None
    for plugin in registry.players():
        if not opens(plugin, "game"):
            continue
        host = player_host(plugin, launcher, store)
        reason = unavailable_reason(plugin, host)
        if reason is None:
            return plugin, host
        first = first or (plugin, reason)
    if first is None:
        raise UserError("没有能启动游戏的插件", status_code=404)
    raise UserError(f"{first[0].name} 不可用：{first[1]}", status_code=503)


def remembers(plugin: PlayerPlugin) -> bool:
    return EXECUTABLES in plugin.Settings.model_fields


def remembered_target(host: PluginPlayerHost, folder: str) -> str | None:
    if not remembers(host.plugin):
        return None
    values = getattr(host.settings, EXECUTABLES, None)
    target = values.get(folder) if isinstance(values, dict) else None
    return target if isinstance(target, str) and target else None


def remember_target(store: ConfigStore, plugin: PlayerPlugin, folder: str, target: str) -> None:
    """只改这个插件设置里 executables 的这一项，别的设置和别的游戏原样保留。"""
    if not remembers(plugin):
        return

    def change(values: dict[str, Any]) -> dict[str, Any]:
        old = values.get(EXECUTABLES)
        executables = dict(old) if isinstance(old, dict) else {}
        executables[folder] = target
        return {**values, EXECUTABLES: executables}

    store.update_plugin_settings(plugin.id, change)
