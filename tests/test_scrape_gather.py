"""内置来源和插件来源共用的取数循环：快照复用、主源失败、取消和各种 outcome。"""

from contextlib import nullcontext
from typing import Any

import pytest

from mortis.errors import UserError
from mortis.scrape.common import SourceMissing, gather
from mortis.tasks.base import TaskCancelled, TaskError


class FakeCtx:
    def __init__(self, cancel_at: int | None = None) -> None:
        self.logs: list[str] = []
        self.checks = 0
        self.cancel_at = cancel_at

    def log(self, message: str) -> None:
        self.logs.append(message)

    def check_cancelled(self) -> None:
        self.checks += 1
        if self.cancel_at is not None and self.checks >= self.cancel_at:
            raise TaskCancelled


class FakeSource:
    def __init__(
        self,
        source_id: str,
        *,
        primary: bool = False,
        skip: tuple[str, str] | None = None,
        error: Exception | None = None,
        missing: str | None = None,
        found: dict[str, str] | None = None,
    ) -> None:
        self.id = source_id
        self.name = source_id.upper()
        self.primary = primary
        self._skip = skip
        self._error = error
        self._missing = missing
        self._found = found or {}
        self.fetches = 0

    def session(self, ctx: Any) -> Any:
        return nullcontext()

    def skip(self) -> tuple[str, str] | None:
        return self._skip

    def fetch(self, ctx: Any, found_ids: dict[str, str]) -> dict[str, Any]:
        self.fetches += 1
        found_ids.update(self._found)
        if self._missing is not None:
            raise SourceMissing(self._missing)
        if self._error is not None:
            raise self._error
        return {"from": self.id}

    def candidates(self, raw: dict[str, Any]) -> dict[str, Any]:
        return {"title": raw["from"]}

    def failure(self, error: Exception) -> str:
        return f"{self.name} 出错，跳过：{error}"


def outcomes(result: Any) -> dict[str, str]:
    return {entry["id"]: entry["outcome"] for entry in result.sources}


def test_snapshot_is_reused_and_only_missing_sources_fetch() -> None:
    cached, fresh = FakeSource("a"), FakeSource("b")
    result = gather(FakeCtx(), [cached, fresh], {"a": {"from": "snap"}})  # type: ignore[arg-type]
    assert cached.fetches == 0 and fresh.fetches == 1
    assert outcomes(result) == {"a": "cached", "b": "ok"}
    assert result.fetched == {"b"}
    assert result.raws == {"a": {"from": "snap"}, "b": {"from": "b"}}
    assert result.candidates["a"] == {"title": "snap"}


def test_secondary_failures_are_recorded_and_primary_failure_propagates() -> None:
    ctx = FakeCtx()
    sources = [
        FakeSource("p", primary=True),
        FakeSource("s", skip=("S 没有启用，跳过", "缺少密钥")),
        FakeSource("m", missing="M 上没有这个条目"),
        FakeSource("f", error=RuntimeError("坏了")),
    ]
    result = gather(ctx, sources, {})  # type: ignore[arg-type]
    assert outcomes(result) == {"p": "ok", "s": "skipped", "m": "not_found", "f": "failed"}
    assert result.sources[1]["detail"] == "缺少密钥"
    assert "S 没有启用，跳过" in ctx.logs
    assert "M 上没有这个条目" in ctx.logs
    assert "F 出错，跳过：坏了" in ctx.logs
    assert set(result.candidates) == {"p"}

    with pytest.raises(UserError):
        gather(FakeCtx(), [FakeSource("p", primary=True, error=UserError("主源挂了"))], {})  # type: ignore[arg-type]
    with pytest.raises(TaskError, match="没有这个条目"):
        gather(FakeCtx(), [FakeSource("p", primary=True, missing="P 没有这个条目")], {})  # type: ignore[arg-type]
    with pytest.raises(TaskError, match="P 不可用：没填 key"):
        gather(FakeCtx(), [FakeSource("p", primary=True, skip=("x", "没填 key"))], {})  # type: ignore[arg-type]


def test_found_ids_survive_a_later_failure_and_cancel_propagates() -> None:
    failing = FakeSource("f", error=RuntimeError("x"), found={"site": "123"})
    result = gather(FakeCtx(), [failing], {})  # type: ignore[arg-type]
    assert result.found_ids == {"site": "123"}

    with pytest.raises(TaskCancelled):
        gather(FakeCtx(cancel_at=2), [FakeSource("a"), FakeSource("b")], {})  # type: ignore[arg-type]
    with pytest.raises(TaskCancelled):
        gather(FakeCtx(), [FakeSource("p", error=TaskCancelled())], {})  # type: ignore[arg-type]
