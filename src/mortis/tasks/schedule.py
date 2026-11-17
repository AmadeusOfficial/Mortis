"""五段 cron 的子集和定时计划的检查循环。"""

import json
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session, sessionmaker

from mortis.db.models import Library, Schedule, Task
from mortis.tasks import repo

logger = logging.getLogger(__name__)

SCAN = "organize"
REFRESH_RUNNING = "refresh_running_shows"
DEFAULT_CRON = "0 3 * * *"
CHECK_SECONDS = 30.0
# 2 月 29 日这类规则最长要等 8 年（跨过不闰的世纪年）。
_SEARCH_YEARS = 8

_FIELDS = (("分钟", 0, 59), ("小时", 0, 23), ("日", 1, 31), ("月", 1, 12), ("星期", 0, 7))


class CronError(ValueError):
    """给用户看的规则错误。"""


@dataclass(frozen=True)
class Cron:
    minutes: frozenset[int]
    hours: frozenset[int]
    days: frozenset[int]
    months: frozenset[int]
    weekdays: frozenset[int]
    any_day: bool
    any_weekday: bool

    def matches_day(self, day: date) -> bool:
        """日和星期都限定时满足其一即可；其中一段以 * 开头时两段都要满足。和 Vixie cron 一样。"""
        day_ok = day.day in self.days
        weekday_ok = (day.weekday() + 1) % 7 in self.weekdays
        if self.any_day or self.any_weekday:
            return day_ok and weekday_ok
        return day_ok or weekday_ok


def _number(text: str, name: str, low: int, high: int) -> int:
    if not text.isdigit():
        raise CronError(f"「{name}」里的 {text} 不是数字")
    value = int(text)
    if not low <= value <= high:
        raise CronError(f"「{name}」的 {value} 超出 {low}~{high}")
    return value


def _parse_field(text: str, name: str, low: int, high: int) -> frozenset[int]:
    values: set[int] = set()
    for part in text.split(","):
        if not part:
            raise CronError(f"「{name}」里有空的一项")
        base, slash, step_text = part.partition("/")
        step = 1
        if slash:
            step = _number(step_text, name, 1, high - low + 1) if step_text else 0
            if step < 1:
                raise CronError(f"「{name}」的步长要大于 0")
        if base == "*":
            start, end = low, high
        elif "-" in base:
            first, _, last = base.partition("-")
            start, end = _number(first, name, low, high), _number(last, name, low, high)
            if start > end:
                raise CronError(f"「{name}」的区间 {base} 前面比后面大")
        else:
            start = _number(base, name, low, high)
            end = high if slash else start
        values.update(range(start, end + 1, step))
    return frozenset(values)


def parse_cron(text: str) -> Cron:
    parts = text.split()
    if len(parts) != len(_FIELDS):
        raise CronError(f"要 5 段（分 时 日 月 周），这里是 {len(parts)} 段")
    parsed = [
        _parse_field(part, name, low, high)
        for part, (name, low, high) in zip(parts, _FIELDS, strict=True)
    ]
    weekdays = frozenset(value % 7 for value in parsed[4])
    return Cron(
        minutes=parsed[0],
        hours=parsed[1],
        days=parsed[2],
        months=parsed[3],
        weekdays=weekdays,
        any_day=parts[2].startswith("*"),
        any_weekday=parts[4].startswith("*"),
    )


def _next_month(moment: datetime) -> datetime:
    if moment.month == 12:
        return datetime(moment.year + 1, 1, 1)
    return datetime(moment.year, moment.month + 1, 1)


def next_after(cron: Cron, after: datetime) -> datetime:
    """after 之后（不含）第一个到点的时刻，按本机时区算，返回带时区的时间。"""
    local = after.astimezone().replace(tzinfo=None) if after.tzinfo else after
    candidate = local.replace(second=0, microsecond=0) + timedelta(minutes=1)
    limit = candidate.replace(year=candidate.year + _SEARCH_YEARS, month=1, day=1)
    while candidate < limit:
        if candidate.month not in cron.months:
            candidate = _next_month(candidate)
        elif not cron.matches_day(candidate.date()):
            candidate = datetime.combine(candidate.date() + timedelta(days=1), datetime.min.time())
        elif candidate.hour not in cron.hours:
            candidate = candidate.replace(minute=0) + timedelta(hours=1)
        elif candidate.minute not in cron.minutes:
            candidate += timedelta(minutes=1)
        else:
            return candidate.astimezone()
    raise CronError("这条规则永远不会到点")


def upcoming(text: str, after: datetime, count: int = 3) -> list[datetime]:
    cron = parse_cron(text)
    result: list[datetime] = []
    moment = after
    for _ in range(count):
        moment = next_after(cron, moment)
        result.append(moment)
    return result


def check_cron(text: str) -> str:
    """规整空白并确认能到点；不合法时抛 CronError。"""
    cleaned = " ".join(text.split())
    next_after(parse_cron(cleaned), datetime.now(UTC))
    return cleaned


def stamp(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat(timespec="seconds")


def _parse_stamp(text: str) -> datetime | None:
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


# ---- 库的计划行 ----


def _library_key() -> Any:
    return func.json_extract(Schedule.payload, "$.library_id")


def library_schedules(session: Session, library_id: int) -> dict[str, Schedule]:
    rows = session.scalars(select(Schedule).where(_library_key() == library_id))
    return {row.task_type: row for row in rows}


def schedules_by_library(
    session: Session, library_ids: list[int]
) -> dict[int, dict[str, Schedule]]:
    result: dict[int, dict[str, Schedule]] = {library_id: {} for library_id in library_ids}
    if not library_ids:
        return result
    for row in session.scalars(select(Schedule).where(_library_key().in_(library_ids))):
        library_id = json.loads(row.payload or "{}").get("library_id")
        if library_id in result:
            result[library_id][row.task_type] = row
    return result


def _schedule_name(library: Library, task_type: str) -> str:
    if task_type == REFRESH_RUNNING:
        return f"刷新「{library.name}」连载中的剧"
    return f"定时扫描「{library.name}」"


def sync_library_schedules(
    session: Session,
    library: Library,
    *,
    cron: str,
    refresh_running: bool,
    now: datetime | None = None,
) -> None:
    """按库的扫描方式写或删它的计划行，只 flush。规则没变的行保留上次和下次时间。"""
    existing = library_schedules(session, library.id)
    wanted: dict[str, str] = {}
    if library.automation == "schedule":
        wanted[SCAN] = cron
        if refresh_running:
            wanted[REFRESH_RUNNING] = cron
    for task_type, row in existing.items():
        if task_type not in wanted:
            session.delete(row)
    moment = now if now is not None else datetime.now(UTC)
    for task_type, text in wanted.items():
        row = existing.get(task_type)
        if row is None:
            row = Schedule(
                task_type=task_type,
                payload=json.dumps({"library_id": library.id}),
                is_enabled=1,
            )
            session.add(row)
        if row.cron != text or row.next_run_at is None:
            row.cron = text
            row.next_run_at = stamp(next_after(parse_cron(text), moment))
        row.name = _schedule_name(library, task_type)
    session.flush()


def delete_library_schedules(session: Session, library_id: int) -> None:
    session.execute(delete(Schedule).where(_library_key() == library_id))


# ---- 检查循环 ----


def _duplicate(session: Session, task_type: str, payload: dict[str, Any]) -> Task | None:
    if "library_id" in payload:
        return repo.find_queued(session, task_type, "library_id", payload["library_id"])
    return session.scalar(
        select(Task).where(Task.task_type == task_type, Task.status == repo.QUEUED).limit(1)
    )


def run_due(session: Session, now: datetime) -> list[int]:
    """把到点的计划排成任务。停机期间错过多少次都只排一次，下次时间从 now 往后算。"""
    created: list[int] = []
    rows = session.scalars(select(Schedule).where(Schedule.is_enabled == 1).order_by(Schedule.id))
    for row in list(rows):
        try:
            cron = parse_cron(row.cron)
            following = stamp(next_after(cron, now))
        except CronError:
            logger.warning("计划 %s 的规则用不了：%s", row.id, row.cron)
            continue
        due = _parse_stamp(row.next_run_at) if row.next_run_at else None
        values: dict[str, Any] = {"next_run_at": following}
        if due is None:
            pass
        elif due > now:
            continue
        else:
            payload = json.loads(row.payload or "{}")
            if _duplicate(session, row.task_type, payload) is None:
                created.append(repo.enqueue(session, row.task_type, payload).id)
            values["last_run_at"] = stamp(now)
        # 用语句更新：接口可能同时删掉这一行，ORM 刷写会因为对不上行数报错。
        session.execute(update(Schedule).where(Schedule.id == row.id).values(**values))
    session.commit()
    return created


class Scheduler:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        wake: Callable[[], None],
        *,
        interval: float = CHECK_SECONDS,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.session_factory = session_factory
        self.interval = interval
        self._wake = wake
        self._clock = clock
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def tick(self) -> list[int]:
        with self.session_factory() as session:
            created = run_due(session, self._clock())
        if created:
            self._wake()
        return created

    def start(self) -> None:
        """启动时先在当前线程查一次，停机期间错过的计划马上补排。"""
        if self._thread is not None:
            return
        self._stop.clear()
        self._safe_tick()
        self._thread = threading.Thread(target=self._loop, name="mortis-schedule", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 10) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)
            self._thread = None

    def _safe_tick(self) -> None:
        try:
            self.tick()
        except Exception:
            logger.exception("检查定时计划出错")

    def _loop(self) -> None:
        while not self._stop.wait(self.interval):
            self._safe_tick()
