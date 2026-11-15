import os
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def default_data_dir() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "Mortis"
    return Path.home() / ".mortis"


class Settings(BaseSettings):
    """进程启动时读一次的运行设置，只来自环境变量。刮削等业务设置不放这里。"""

    model_config = SettingsConfigDict(env_prefix="MORTIS_")

    data_dir: Path = Field(default_factory=default_data_dir)
    host: str = "127.0.0.1"
    port: int = Field(default=8100, ge=1, le=65535)
    web_dist: Path | None = None
