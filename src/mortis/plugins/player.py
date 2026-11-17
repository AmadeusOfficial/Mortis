"""播放插件拿到的宿主，以及按设置选出要用的播放插件。"""

import logging
from collections.abc import Callable
from pathlib import Path

from mortis.config.models import AppConfig
from mortis.config.store import ConfigStore
from mortis.errors import UserError
from mortis.plugin_api import PlayerError, PlayerPlugin
from mortis.plugins.host import plugin_settings
from mortis.plugins.launch import Launcher
from mortis.plugins.registry import PluginRegistry

logger = logging.getLogger("mortis.play")


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

    def launch(self, executable: str, args: list[str]) -> None:
        if not isinstance(executable, str) or not executable.strip():
            raise PlayerError("没有要启动的程序")
        if not isinstance(args, list) or not all(isinstance(arg, str) for arg in args):
            raise PlayerError("启动参数只能是字符串列表")
        self.log(f"启动 {executable} {args}")
        try:
            self._launcher.launch(executable, list(args))
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


def choose_player(
    registry: PluginRegistry, launcher: Launcher, store: ConfigStore
) -> tuple[PlayerPlugin, PluginPlayerHost]:
    """设置里选了就用它；没选时用第一个可用的。选了但用不了时报原因，不换别的。"""
    chosen = store.load_config().playback.player
    if chosen:
        plugin = registry.player(chosen)
        if plugin is None:
            info = registry.get(chosen)
            if info is None or info.kind != "player":
                raise UserError(
                    f"没有播放插件「{chosen}」，在设置的「播放」里换一个", status_code=503
                )
            if not info.enabled:
                raise UserError(f"播放插件「{info.name}」已停用", status_code=503)
            raise UserError(f"播放插件「{info.name}」没有加载：{info.error}", status_code=503)
        host = player_host(plugin, launcher, store)
        reason = unavailable_reason(plugin, host)
        if reason is not None:
            raise UserError(f"{plugin.name} 不可用：{reason}", status_code=503)
        return plugin, host
    for plugin in registry.players():
        host = player_host(plugin, launcher, store)
        if unavailable_reason(plugin, host) is None:
            return plugin, host
    raise UserError("没有可用的播放插件", status_code=503)
