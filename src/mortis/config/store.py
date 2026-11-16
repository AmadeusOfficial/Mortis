import os
import tempfile
import threading
import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import Any

import tomli_w
from pydantic import BaseModel, ValidationError

from mortis.config.models import (
    BUILTIN_TYPE_SECTIONS,
    CONFIG_SECTIONS,
    TMDB_SOURCE,
    TYPES_PREFIX,
    AppConfig,
    PluginTypeConfig,
    Secrets,
    validation_problems,
)

CONFIG_FILE = "config.toml"
SECRETS_FILE = "secrets.toml"
CONFIG_BACKUP = "config.toml.bak"
SECRETS_BACKUP = "secrets.toml.bak"

OLD_TYPE_SECTIONS = ("movie", "tv", "anime")
"""旧版设置文件里这几种数据类型的节直接放在顶层，迁移时搬到 types 下面。"""
OLD_SECRET_KEYS = ("tmdb_api_key", "proxy_username", "proxy_password")


class ConfigError(Exception):
    pass


class ConfigStore:
    def __init__(self, data_dir: Path) -> None:
        self.config_path = data_dir / CONFIG_FILE
        self.secrets_path = data_dir / SECRETS_FILE
        self._lock = threading.RLock()

    def load_config(self) -> AppConfig:
        with self._lock:
            return _validate(self.config_path, self._config_raw(), AppConfig)

    def save_config(self, config: AppConfig) -> None:
        with self._lock:
            raw = self._config_raw()
            raw.update(config.model_dump(mode="json"))
            _write(self.config_path, raw)

    def save_section(self, name: str, section: BaseModel) -> AppConfig:
        """只替换一节；其他节和文件里还不认识的键原样保留。

        `types.<id>` 替换 types 下某一节，其他类型不动。
        """
        with self._lock:
            raw = self._config_raw()
            if name.startswith(TYPES_PREFIX):
                self._save_type_section(raw, name.removeprefix(TYPES_PREFIX), section)
            else:
                expected = CONFIG_SECTIONS.get(name)
                if expected is None or not isinstance(section, expected):
                    raise ValueError(f"没有这一节设置：{name}")
                old = raw.get(name)
                merged = dict(old) if isinstance(old, dict) else {}
                merged.update(section.model_dump(mode="json"))
                raw[name] = merged
            config = _validate(self.config_path, raw, AppConfig)
            _write(self.config_path, raw)
            return config

    @staticmethod
    def _save_type_section(raw: dict[str, Any], data_type_id: str, section: BaseModel) -> None:
        expected = BUILTIN_TYPE_SECTIONS.get(data_type_id, PluginTypeConfig)
        if not data_type_id or not isinstance(section, expected):
            raise ValueError(f"没有这一节设置：{data_type_id}")
        types = raw.get("types")
        types = dict(types) if isinstance(types, dict) else {}
        old = types.get(data_type_id)
        merged = dict(old) if isinstance(old, dict) else {}
        merged.update(section.model_dump(mode="json"))
        types[data_type_id] = merged
        raw["types"] = types

    def save_plugin(
        self, plugin_id: str, *, values: dict[str, Any] | None = None, disabled: bool | None = None
    ) -> AppConfig:
        with self._lock:
            raw = self._config_raw()
            if values is not None:
                sources = raw.get("sources")
                sources = dict(sources) if isinstance(sources, dict) else {}
                sources[plugin_id] = values
                raw["sources"] = sources
            if disabled is not None:
                plugins = raw.get("plugins")
                plugins = dict(plugins) if isinstance(plugins, dict) else {}
                old = plugins.get("disabled")
                names = [name for name in old if name != plugin_id] if isinstance(old, list) else []
                plugins["disabled"] = [*names, plugin_id] if disabled else names
                raw["plugins"] = plugins
            config = _validate(self.config_path, raw, AppConfig)
            _write(self.config_path, raw)
            return config

    def load_secrets(self) -> Secrets:
        with self._lock:
            return _validate(self.secrets_path, self._secrets_raw(), Secrets)

    def save_secrets(self, secrets: Secrets) -> None:
        with self._lock:
            raw = self._secrets_raw()
            raw.update(secrets.model_dump(mode="json"))
            _write(self.secrets_path, raw)

    def update_plugin_secrets(self, plugin_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        """只改给出的键。令牌续期和设置页保存可能同时发生，读改写在锁里做。"""
        with self._lock:
            raw = self._secrets_raw()
            secrets = _validate(self.secrets_path, raw, Secrets)
            values = {**secrets.sources.get(plugin_id, {}), **changes}
            secrets.sources[plugin_id] = values
            raw.update(secrets.model_dump(mode="json"))
            _write(self.secrets_path, raw)
            return values

    def _config_raw(self) -> dict[str, Any]:
        return self._migrated_raw(self.config_path, CONFIG_BACKUP, migrate_config)

    def _secrets_raw(self) -> dict[str, Any]:
        return self._migrated_raw(self.secrets_path, SECRETS_BACKUP, migrate_secrets)

    def _migrated_raw(
        self,
        path: Path,
        backup_name: str,
        migrate: Callable[[dict[str, Any]], dict[str, Any] | None],
    ) -> dict[str, Any]:
        """读文件；旧格式先备份成同目录的 .bak，再写回新结构。新格式不会写文件。"""
        raw = _read_raw(path)
        migrated = migrate(raw)
        if migrated is None:
            return raw
        _backup(path, path.parent / backup_name)
        _write(path, migrated)
        return migrated


def migrate_config(raw: dict[str, Any]) -> dict[str, Any] | None:
    """旧格式的普通设置转成新结构；已经是新结构时返回 None。不认识的键原样保留。"""
    if not _config_is_old(raw):
        return None
    migrated = dict(raw)

    types = migrated.get("types")
    types = dict(types) if isinstance(types, dict) else {}
    for type_id in OLD_TYPE_SECTIONS:
        old = migrated.pop(type_id, None)
        if old is None:
            continue
        merged = dict(old) if isinstance(old, dict) else {}
        current = types.get(type_id)
        if isinstance(current, dict):
            # 新旧键都在时以新键为准。
            merged.update(current)
        types[type_id] = merged
    if types:
        migrated["types"] = types

    old_plugins = migrated.pop("plugins", None)
    old_disabled = migrated.pop("disabled_plugins", None)
    disabled: list[str] = []
    plugin_settings: dict[str, Any] = {}
    if isinstance(old_plugins, dict):
        for plugin_id, values in old_plugins.items():
            if plugin_id == "disabled":
                if isinstance(values, list):
                    disabled.extend(name for name in values if isinstance(name, str))
                continue
            plugin_settings[plugin_id] = values
    if plugin_settings:
        sources = migrated.get("sources")
        sources = dict(sources) if isinstance(sources, dict) else {}
        for plugin_id, values in plugin_settings.items():
            merged = dict(values) if isinstance(values, dict) else {}
            current = sources.get(plugin_id)
            if isinstance(current, dict):
                merged.update(current)
            sources[plugin_id] = merged
        migrated["sources"] = sources
    if isinstance(old_disabled, list):
        disabled.extend(name for name in old_disabled if isinstance(name, str))
    if old_plugins is not None or old_disabled is not None:
        unique: list[str] = []
        for name in disabled:
            if name not in unique:
                unique.append(name)
        migrated["plugins"] = {"disabled": unique}
    return migrated


def migrate_secrets(raw: dict[str, Any]) -> dict[str, Any] | None:
    """旧格式的密钥转成新结构；已经是新结构时返回 None。"""
    if not _secrets_is_old(raw):
        return None
    migrated = dict(raw)

    network = migrated.get("network")
    network = dict(network) if isinstance(network, dict) else {}
    for key in ("proxy_username", "proxy_password"):
        old = migrated.pop(key, None)
        if old is not None and key not in network:
            network[key] = old
    if network:
        migrated["network"] = network

    sources = migrated.get("sources")
    sources = dict(sources) if isinstance(sources, dict) else {}
    old_key = migrated.pop("tmdb_api_key", None)
    if old_key is not None:
        tmdb = sources.get(TMDB_SOURCE)
        tmdb = dict(tmdb) if isinstance(tmdb, dict) else {}
        tmdb.setdefault("api_key", old_key)
        sources[TMDB_SOURCE] = tmdb

    old_plugins = migrated.pop("plugins", None)
    if isinstance(old_plugins, dict):
        for plugin_id, values in old_plugins.items():
            merged = dict(values) if isinstance(values, dict) else {}
            current = sources.get(plugin_id)
            if isinstance(current, dict):
                merged.update(current)
            sources[plugin_id] = merged
    if sources:
        migrated["sources"] = sources
    return migrated


def _config_is_old(raw: dict[str, Any]) -> bool:
    if any(key in raw for key in (*OLD_TYPE_SECTIONS, "disabled_plugins")):
        return True
    plugins = raw.get("plugins")
    # 新结构的 [plugins] 只有 disabled 一项，其余键都是旧版按插件 id 分的节。
    return isinstance(plugins, dict) and any(key != "disabled" for key in plugins)


def _secrets_is_old(raw: dict[str, Any]) -> bool:
    return any(key in raw for key in OLD_SECRET_KEYS) or isinstance(raw.get("plugins"), dict)


def _backup(path: Path, backup: Path) -> None:
    """迁移前留一份原文；已经有备份就不再覆盖。"""
    if backup.exists():
        return
    try:
        payload = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as error:
        raise ConfigError(f"{path} 不是 UTF-8 编码，无法读取") from error
    except OSError as error:
        raise ConfigError(f"{path} 无法读取：{error.strerror or type(error).__name__}") from error
    _write_atomic(backup, payload)


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
        raise ConfigError(f"{path} 里有不合法的设置：{validation_problems(error)}") from None


def _write(path: Path, data: dict[str, Any]) -> None:
    _write_atomic(path, tomli_w.dumps(data))


def _write_atomic(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(dir=path.parent, prefix=f"{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
        os.replace(temp_name, path)
    except BaseException:
        Path(temp_name).unlink(missing_ok=True)
        raise
