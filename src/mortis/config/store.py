import os
import tempfile
import tomllib
from pathlib import Path

import tomli_w
from pydantic import BaseModel, ValidationError

from mortis.config.models import AppConfig, Secrets

CONFIG_FILE = "config.toml"
SECRETS_FILE = "secrets.toml"


class ConfigError(Exception):
    pass


class ConfigStore:
    def __init__(self, data_dir: Path) -> None:
        self.config_path = data_dir / CONFIG_FILE
        self.secrets_path = data_dir / SECRETS_FILE

    def load_config(self) -> AppConfig:
        return _load(self.config_path, AppConfig)

    def save_config(self, config: AppConfig) -> None:
        _save(self.config_path, config)

    def load_secrets(self) -> Secrets:
        return _load(self.secrets_path, Secrets)

    def save_secrets(self, secrets: Secrets) -> None:
        _save(self.secrets_path, secrets)


def _load[M: BaseModel](path: Path, model: type[M]) -> M:
    if not path.is_file():
        return model()
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as error:
        raise ConfigError(f"{path} 不是合法的 TOML：{error}") from error
    try:
        return model.model_validate(data)
    except ValidationError as error:
        raise ConfigError(f"{path} 里有不合法的设置：{error}") from error


def _save(path: Path, model: BaseModel) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = tomli_w.dumps(model.model_dump(mode="json"))
    fd, temp_name = tempfile.mkstemp(dir=path.parent, prefix=f"{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
        os.replace(temp_name, path)
    except BaseException:
        Path(temp_name).unlink(missing_ok=True)
        raise
