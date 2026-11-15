from collections import deque
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path

LOG_DIR = Path("logs") / "tasks"


def task_log_path(data_dir: Path, task_id: int) -> Path:
    return data_dir / LOG_DIR / f"task-{task_id}.log"


def append_log(path: Path, message: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        for line in message.splitlines() or [""]:
            handle.write(f"{stamp} {line}\n")


def tail_log(path: Path, limit: int) -> list[str]:
    if not path.is_file():
        return []
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        return [line.rstrip("\n") for line in deque(handle, maxlen=limit)]


def remove_logs(data_dir: Path, task_ids: Iterable[int]) -> None:
    for task_id in task_ids:
        task_log_path(data_dir, task_id).unlink(missing_ok=True)
