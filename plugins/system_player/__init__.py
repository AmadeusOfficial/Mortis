"""用系统给视频文件设的默认程序打开。"""

import os

from mortis.plugin_api import PlayerHost, PlayerPlugin


class SystemPlayer(PlayerPlugin):
    id = "system_player"
    name = "系统默认播放器"

    def unavailable_reason(self, host: PlayerHost) -> str | None:
        return None if hasattr(os, "startfile") else "这个系统不能用默认程序打开文件"

    def play(self, host: PlayerHost, path: str) -> None:
        host.open_default(path)


PLUGIN = SystemPlayer()
