"""插件 Settings 发给设置页表单前的 schema 规范化，以及写进 config.toml 前的整理。"""

from typing import Any

from pydantic import BaseModel

NULL = {"type": "null"}
UNIONS = ("anyOf", "oneOf")


def form_schema(model: type[BaseModel]) -> dict[str, Any]:
    """`X | None` 生成的 `anyOf: [X, {type: null}]` 收成 X，表单里不出现「选类型」。

    `$defs`、`$ref` 原样保留，由表单库解析。
    """
    normalized = _normalize(model.model_json_schema())
    assert isinstance(normalized, dict)
    return normalized


def _normalize(node: Any) -> Any:
    if isinstance(node, list):
        return [_normalize(item) for item in node]
    if not isinstance(node, dict):
        return node
    result = {key: _normalize(value) for key, value in node.items()}
    for key in UNIONS:
        branches = result.get(key)
        if not isinstance(branches, list) or len(branches) != 2 or NULL not in branches:
            continue
        (kept,) = [branch for branch in branches if branch != NULL]
        outer = {k: v for k, v in result.items() if k != key}
        if outer.get("default", ...) is None:
            del outer["default"]
        return {**kept, **outer} if isinstance(kept, dict) else outer
    return result


def storable(values: Any) -> Any:
    """TOML 没有 null：递归去掉值为 None 的键，读回时由模型默认值补齐。"""
    if isinstance(values, dict):
        return {key: storable(value) for key, value in values.items() if value is not None}
    if isinstance(values, list):
        return [storable(item) for item in values]
    return values
