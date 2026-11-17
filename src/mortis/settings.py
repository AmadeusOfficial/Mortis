import os
import sys
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def default_data_dir() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "Mortis"
    return Path.home() / ".mortis"


def app_root() -> Path:
    """打包后是 zip 解压出的目录（onedir\\Mortis.Server.exe 的上一级），源码运行时是仓库根目录。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent.parent
    return Path(__file__).resolve().parents[2]


def default_plugins_dir() -> Path | None:
    bundled = app_root() / "plugins"
    return bundled if bundled.is_dir() else None


def default_web_dist() -> Path | None:
    """只有打包后才默认托管前端；源码运行时由启动脚本用 MORTIS_WEB_DIST 指定。"""
    if not getattr(sys, "frozen", False):
        return None
    dist = app_root() / "web" / "dist"
    return dist if (dist / "index.html").is_file() else None


def _is_blank(value: object) -> bool:
    return isinstance(value, str) and not value.strip()


class Settings(BaseSettings):
    """进程启动时读一次的运行设置，只来自环境变量。刮削等业务设置不放这里。"""

    model_config = SettingsConfigDict(env_prefix="MORTIS_")

    data_dir: Path = Field(default_factory=default_data_dir)
    host: str = "127.0.0.1"
    port: int = Field(default=8100, ge=1, le=65535)
    web_dist: Path | None = Field(default_factory=default_web_dist)
    plugins_dir: Path | None = Field(default_factory=default_plugins_dir)
    """程序自带的插件目录。数据目录下的 plugins 另外加载，同 id 时覆盖这里的。"""

    @field_validator("data_dir", mode="before")
    @classmethod
    def _blank_data_dir_is_default(cls, value: object) -> object:
        return default_data_dir() if _is_blank(value) else value

    @field_validator("web_dist", "plugins_dir", mode="before")
    @classmethod
    def _blank_web_dist_is_unset(cls, value: object) -> object:
        return None if _is_blank(value) else value
