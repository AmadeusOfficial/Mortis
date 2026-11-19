"""钩子的运行状态：连续失败次数和熔断。

状态在进程里（挂在注册表上，设置页从注册表读）；熔断时钩子 id 同时写进设置文件的
`[plugins] disabled`，重启后注册表按它把这个钩子当作已停用。
"""

import threading
from dataclasses import dataclass, replace

FUSE_LIMIT = 3
"""连续失败这么多次，自动停用这个钩子。"""
FALLBACK_REASON = "已停用（可能是连续失败后自动停用，重新启用后会再试）"


@dataclass(frozen=True)
class HookStatus:
    """设置页显示用。disabled 为真时钩子不会再收到事件。"""

    disabled: bool = False
    reason: str | None = None
    """被熔断停用的原因；手动停用或重启后丢了原因时为通用说明。"""
    failures: int = 0
    """当前连续失败次数，成功一次清零。"""
    last_error: str | None = None
    runs: int = 0
    """进程启动以来跑过的次数。"""


class HookHealth:
    def __init__(self) -> None:
        self._guard = threading.Lock()
        self._state: dict[str, HookStatus] = {}

    def status(self, hook_id: str, *, off: bool = False) -> HookStatus:
        """off 是设置文件里已停用的钩子（来自注册表）。"""
        with self._guard:
            state = self._state.get(hook_id, HookStatus())
        if state.disabled or not off:
            return state
        return replace(state, disabled=True, reason=state.reason or FALLBACK_REASON)

    def tripped(self, hook_id: str) -> bool:
        with self._guard:
            return self._state.get(hook_id, HookStatus()).disabled

    def tripped_ids(self) -> list[str]:
        with self._guard:
            return [hook_id for hook_id, state in self._state.items() if state.disabled]

    def succeeded(self, hook_id: str) -> None:
        with self._guard:
            old = self._state.get(hook_id, HookStatus())
            self._state[hook_id] = replace(old, failures=0, runs=old.runs + 1)

    def failed(self, hook_id: str, error: str, *, limit: int = FUSE_LIMIT) -> bool:
        """记一次失败。达到上限时熔断并返回 True（只有这一次返回 True）。"""
        with self._guard:
            old = self._state.get(hook_id, HookStatus())
            failures = old.failures + 1
            blown = failures >= limit and not old.disabled
            reason = f"连续失败 {failures} 次，已自动停用：{error}" if blown else old.reason
            self._state[hook_id] = replace(
                old,
                failures=failures,
                last_error=error,
                runs=old.runs + 1,
                disabled=old.disabled or blown,
                reason=reason,
            )
            return blown

    def reset(self, hook_id: str) -> None:
        """重新启用时清掉失败记录和熔断。"""
        with self._guard:
            self._state.pop(hook_id, None)
