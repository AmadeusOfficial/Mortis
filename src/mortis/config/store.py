import os
import tempfile
import threading
import tomllib
from pathlib import Path
from typing import Any

import tomli_w
from pydantic import BaseModel, ValidationError

from mortis.config.models import CONFIG_SECTIONS, AppConfig, Secrets

CONFIG_FILE = "config.toml"
SECRETS_FILE = "secrets.toml"


class ConfigError(Exception):
    pass


class ConfigStore:
    def __init__(self, data_dir: Path) -> None:
        self.config_path = data_dir / CONFIG_FILE
        self.secrets_path = data_dir / SECRETS_FILE
        self._lock = threading.RLock()

    def load_config(self) -> AppConfig:
        return _load(self.config_path, AppConfig)

    def save_config(self, config: AppConfig) -> None:
        _save(self.config_path, config)

    def save_section(self, name: str, section: BaseModel) -> AppConfig:
        """只替换一节；其他节和文件里还不认识的键原样保留。"""
        expected = CONFIG_SECTIONS.get(name)
        if expected is None or not isinstance(section, expected):
            raise ValueError(f"没有这一节设置：{name}")
        raw = _read_raw(self.config_path)
        old = raw.get(name)
        merged = dict(old) if isinstance(old, dict) else {}
        merged.update(section.model_dump(mode="json"))
        raw[name] = merged
        config = _validate(self.config_path, raw, AppConfig)
        _write(self.config_path, raw)
        return config

    def save_plugin(
        self, plugin_id: str, *, values: dict[str, Any] | None = None, disabled: bool | None = None
    ) -> AppConfig:
        with self._lock:
            raw = _read_raw(self.config_path)
            if values is not None:
                plugins = raw.get("plugins")
                plugins = dict(plugins) if isinstance(plugins, dict) else {}
                plugins[plugin_id] = values
                raw["plugins"] = plugins
            if disabled is not None:
                old = raw.get("disabled_plugins")
                names = [name for name in old if name != plugin_id] if isinstance(old, list) else []
                raw["disabled_plugins"] = [*names, plugin_id] if disabled else names
            config = _validate(self.config_path, raw, AppConfig)
            _write(self.config_path, raw)
            return config

    def load_secrets(self) -> Secrets:
        return _load(self.secrets_path, Secrets)

    def save_secrets(self, secrets: Secrets) -> None:
        with self._lock:
            _save(self.secrets_path, secrets)

    def update_plugin_secrets(self, plugin_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        """只改给出的键。令牌续期和设置页保存可能同时发生，读改写在锁里做。"""
        with self._lock:
            secrets = self.load_secrets()
            values = {**secrets.plugins.get(plugin_id, {}), **changes}
            secrets.plugins[plugin_id] = values
            _save(self.secrets_path, secrets)
            return values


def _read_raw(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except UnicodeDecodeError as error:
        raise ConfigError(f"{path} 不是 UTF-8 编码，无法读取") from error
    except OSError as error:
        raise ConfigError(f"{path} 无法读取：{error.strerror or type(error).__name__}") from error
    except tomllib.TOMLDecodeError as error:
        raise ConfigError(f"{path} 不是合法的 TOML：{error}") from error


def _validate[M: BaseModel](path: Path, data: dict[str, Any], model: type[M]) -> M:
    try:
        return model.model_validate(data)
    except ValidationError as error:
        # 只用字段位置和原因，不带输入值，避免密钥泄漏到错误消息里。
        problems = "；".join(
            f"{'.'.join(str(part) for part in item['loc'])}：{item['msg']}"
            for item in error.errors(include_input=False, include_url=False)
        )
        raise ConfigError(f"{path} 里有不合法的设置：{problems}") from None


def _load[M: BaseModel](path: Path, model: type[M]) -> M:
    if not path.is_file():
        return model()
    return _validate(path, _read_raw(path), model)


def _save(path: Path, model: BaseModel) -> None:
    _write(path, model.model_dump(mode="json"))


def _write(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = tomli_w.dumps(data)
    fd, temp_name = tempfile.mkstemp(dir=path.parent, prefix=f"{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
        os.replace(temp_name, path)
    except BaseException:
        Path(temp_name).unlink(missing_ok=True)
        raise
