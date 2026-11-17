import json
import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, sessionmaker

from mortis.api.app import create_app
from mortis.db.engine import make_session_factory
from mortis.db.models import Schedule, Task
from mortis.settings import Settings
from mortis.tasks import repo
from mortis.tasks.schedule import (
    CronError,
    Scheduler,
    check_cron,
    next_after,
    parse_cron,
    run_due,
    stamp,
)

LOCAL = "http://127.0.0.1"


def _next(cron: str, after: datetime) -> datetime:
    return next_after(parse_cron(cron), after).replace(tzinfo=None)


@pytest.mark.parametrize(
    ("cron", "after", "expected"),
    [
        ("0 3 * * *", datetime(2026, 11, 17, 10, 0), datetime(2026, 11, 18, 3, 0)),
        ("0 3 * * *", datetime(2026, 11, 17, 2, 59, 30), datetime(2026, 11, 17, 3, 0)),
        ("0 3 * * *", datetime(2026, 11, 17, 3, 0), datetime(2026, 11, 18, 3, 0)),
        ("*/15 * * * *", datetime(2026, 11, 17, 10, 7), datetime(2026, 11, 17, 10, 15)),
        ("0 */6 * * *", datetime(2026, 11, 17, 10, 0), datetime(2026, 11, 17, 12, 0)),
        ("0 */6 * * *", datetime(2026, 11, 17, 23, 0), datetime(2026, 11, 18, 0, 0)),
        ("5,10-20/5 * * * *", datetime(2026, 11, 17, 10, 6), datetime(2026, 11, 17, 10, 10)),
        ("5,10-20/5 * * * *", datetime(2026, 11, 17, 10, 20), datetime(2026, 11, 17, 11, 5)),
        # 跨月、跨年、大小月
        ("30 2 1 * *", datetime(2026, 11, 17, 10, 0), datetime(2026, 12, 1, 2, 30)),
        ("0 0 31 * *", datetime(2026, 11, 1, 0, 0), datetime(2026, 12, 31, 0, 0)),
        ("0 0 1 1 *", datetime(2026, 12, 31, 23, 59), datetime(2027, 1, 1, 0, 0)),
        ("0 0 29 2 *", datetime(2026, 3, 1, 0, 0), datetime(2028, 2, 29, 0, 0)),
        ("0 12 * 3-4 *", datetime(2026, 11, 17, 10, 0), datetime(2027, 3, 1, 12, 0)),
        # 2026-11-17 是周二
        ("0 3 * * 1", datetime(2026, 11, 17, 10, 0), datetime(2026, 11, 23, 3, 0)),
        ("0 0 * * 7", datetime(2026, 11, 17, 10, 0), datetime(2026, 11, 22, 0, 0)),
        ("0 0 * * 0", datetime(2026, 11, 17, 10, 0), datetime(2026, 11, 22, 0, 0)),
        ("0 8 * * 1-5", datetime(2026, 11, 20, 9, 0), datetime(2026, 11, 23, 8, 0)),
        # 日和星期都给：满足其一
        ("0 9 1 * 5", datetime(2026, 11, 17, 10, 0), datetime(2026, 11, 20, 9, 0)),
        ("0 9 1 * 5", datetime(2026, 11, 27, 10, 0), datetime(2026, 12, 1, 9, 0)),
        # 有一段以 * 开头时两段都要满足：单数日且是周一
        ("0 0 */2 * 1", datetime(2026, 11, 17, 10, 0), datetime(2026, 11, 23, 0, 0)),
    ],
)
def test_next_time(cron: str, after: datetime, expected: datetime) -> None:
    assert _next(cron, after) == expected


def test_next_time_uses_local_timezone() -> None:
    after = datetime(2026, 11, 17, 10, 0).astimezone()
    result = next_after(parse_cron("0 3 * * *"), after.astimezone(UTC))
    assert result.tzinfo is not None
    assert result.utcoffset() == after.utcoffset()
    assert (result.hour, result.minute) == (3, 0)


@pytest.mark.parametrize(
    ("cron", "message"),
    [
        ("0 3 * *", "要 5 段"),
        ("60 * * * *", "「分钟」的 60 超出 0~59"),
        ("* 24 * * *", "「小时」的 24 超出 0~23"),
        ("* * 0 * *", "「日」的 0 超出 1~31"),
        ("* * * 13 *", "「月」的 13 超出 1~12"),
        ("* * * * 8", "「星期」的 8 超出 0~7"),
        ("5-1 * * * *", "前面比后面大"),
        ("*/0 * * * *", "超出"),
        ("*/ * * * *", "步长要大于 0"),
        ("a * * * *", "不是数字"),
        ("1,,2 * * * *", "空的一项"),
        ("0 0 31 2 *", "永远不会到点"),
    ],
)
def test_bad_cron(cron: str, message: str) -> None:
    with pytest.raises(CronError, match=message):
        check_cron(cron)


def test_check_cron_tidies_spaces() -> None:
    assert check_cron("  0   3 * *  * ") == "0 3 * * *"


@pytest.fixture
def factory(engine: Engine) -> sessionmaker[Session]:
    return make_session_factory(engine)


def _schedule(factory: sessionmaker[Session], next_run_at: str | None, library_id: int = 1) -> int:
    with factory() as session:
        row = Schedule(
            name="定时扫描",
            cron="0 3 * * *",
            task_type="organize",
            payload=json.dumps({"library_id": library_id}),
            is_enabled=1,
            next_run_at=next_run_at,
        )
        session.add(row)
        session.commit()
        return row.id


def _tasks(factory: sessionmaker[Session]) -> list[Task]:
    with factory() as session:
        rows = list(session.scalars(select(Task).order_by(Task.id)))
        session.expunge_all()
        return rows


def test_missed_runs_only_run_once(factory: sessionmaker[Session]) -> None:
    now = datetime(2026, 11, 17, 10, 0, tzinfo=UTC)
    schedule_id = _schedule(factory, stamp(now - timedelta(days=3)))
    created = run_due(factory(), now)
    assert len(created) == 1
    tasks = _tasks(factory)
    assert [(task.task_type, json.loads(task.payload or "")) for task in tasks] == [
        ("organize", {"library_id": 1})
    ]
    with factory() as session:
        row = session.get(Schedule, schedule_id)
        assert row is not None
        assert row.last_run_at == stamp(now)
        assert row.next_run_at == stamp(next_after(parse_cron("0 3 * * *"), now))
    assert run_due(factory(), now + timedelta(minutes=1)) == []
    assert len(_tasks(factory)) == 1


def test_first_check_only_sets_next_time(factory: sessionmaker[Session]) -> None:
    now = datetime(2026, 11, 17, 10, 0, tzinfo=UTC)
    schedule_id = _schedule(factory, None)
    assert run_due(factory(), now) == []
    with factory() as session:
        row = session.get(Schedule, schedule_id)
        assert row is not None
        assert row.next_run_at is not None and row.last_run_at is None


def test_queued_scan_is_not_queued_again(factory: sessionmaker[Session]) -> None:
    now = datetime(2026, 11, 17, 10, 0, tzinfo=UTC)
    with factory() as session:
        repo.enqueue(session, "organize", {"library_id": 1, "scrape": None})
        repo.enqueue(session, "organize", {"library_id": 2, "scrape": None})
        session.commit()
    schedule_id = _schedule(factory, stamp(now - timedelta(minutes=1)))
    assert run_due(factory(), now) == []
    assert len(_tasks(factory)) == 2
    with factory() as session:
        row = session.get(Schedule, schedule_id)
        assert row is not None and row.next_run_at is not None
        assert row.next_run_at > stamp(now)


def test_disabled_schedule_is_skipped(factory: sessionmaker[Session]) -> None:
    now = datetime(2026, 11, 17, 10, 0, tzinfo=UTC)
    schedule_id = _schedule(factory, stamp(now - timedelta(days=1)))
    with factory() as session:
        row = session.get(Schedule, schedule_id)
        assert row is not None
        row.is_enabled = 0
        session.commit()
    assert run_due(factory(), now) == []


def test_scheduler_thread_picks_up_due_schedule_and_stops(
    factory: sessionmaker[Session],
) -> None:
    woke: list[bool] = []
    scheduler = Scheduler(factory, lambda: woke.append(True), interval=0.05)
    scheduler.start()
    try:
        _schedule(factory, stamp(datetime.now(UTC) - timedelta(hours=1)))
        deadline = time.monotonic() + 5
        while not woke and time.monotonic() < deadline:
            time.sleep(0.02)
    finally:
        started = time.monotonic()
        scheduler.stop(timeout=2)
        assert time.monotonic() - started < 1
    assert woke
    assert len(_tasks(factory)) == 1


# ---- 媒体库接口 ----


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    app = create_app(Settings(data_dir=tmp_path / "data"), start_worker=False)
    with TestClient(app, base_url=LOCAL) as opened:
        yield opened


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    path = tmp_path / "片库"
    path.mkdir()
    return path


def _rows(client: TestClient) -> list[tuple[str, str, dict]]:
    with client.app.state.session_factory() as session:  # type: ignore[attr-defined]
        return [
            (row.task_type, row.cron, json.loads(row.payload or "{}"))
            for row in session.scalars(select(Schedule).order_by(Schedule.id))
        ]


def test_library_schedule_rows_follow_automation(client: TestClient, folder: Path) -> None:
    body = client.post(
        "/api/libraries",
        json={
            "name": "剧",
            "data_type_id": "tv",
            "paths": [str(folder)],
            "automation": "schedule",
            "schedule_cron": "0 */6 * * *",
            "refresh_running": True,
        },
    ).json()
    library_id = body["id"]
    assert body["automation"] == "schedule"
    assert body["schedule_cron"] == "0 */6 * * *"
    assert body["refresh_running"] is True
    assert body["next_scan_at"] is not None
    assert _rows(client) == [
        ("organize", "0 */6 * * *", {"library_id": library_id}),
        ("refresh_running_shows", "0 */6 * * *", {"library_id": library_id}),
    ]

    renamed = client.patch(f"/api/libraries/{library_id}", json={"name": "电视剧"}).json()
    assert renamed["schedule_cron"] == "0 */6 * * *"
    assert renamed["next_scan_at"] == body["next_scan_at"]

    changed = client.patch(
        f"/api/libraries/{library_id}",
        json={"schedule_cron": "0 3 * * 1", "refresh_running": False},
    ).json()
    assert changed["refresh_running"] is False
    assert _rows(client) == [("organize", "0 3 * * 1", {"library_id": library_id})]

    manual = client.patch(f"/api/libraries/{library_id}", json={"automation": "manual"}).json()
    assert (manual["schedule_cron"], manual["next_scan_at"]) == (None, None)
    assert _rows(client) == []

    client.patch(f"/api/libraries/{library_id}", json={"automation": "schedule"})
    assert _rows(client) == [("organize", "0 3 * * *", {"library_id": library_id})]
    assert client.delete(f"/api/libraries/{library_id}").status_code == 204
    assert _rows(client) == []


def test_library_schedule_rejects_bad_input(client: TestClient, folder: Path) -> None:
    created = client.post(
        "/api/libraries", json={"name": "电影", "data_type_id": "movie", "paths": [str(folder)]}
    ).json()
    bad = client.patch(
        f"/api/libraries/{created['id']}",
        json={"automation": "schedule", "schedule_cron": "0 25 * * *"},
    )
    assert bad.status_code == 422
    assert bad.json()["detail"] == "定时规则不对：「小时」的 25 超出 0~23"
    refresh = client.patch(
        f"/api/libraries/{created['id']}",
        json={"automation": "schedule", "refresh_running": True},
    )
    assert refresh.status_code == 422
    assert refresh.json()["detail"] == "只有剧和动漫库能刷新连载中的剧"
    assert client.get(f"/api/libraries/{created['id']}").json()["automation"] == "manual"
    assert _rows(client) == []


def test_cron_preview(client: TestClient) -> None:
    response = client.get("/api/schedules/preview", params={"cron": "0  3 * * *"})
    assert response.status_code == 200
    body = response.json()
    assert body["cron"] == "0 3 * * *"
    assert len(body["next_runs"]) == 3
    assert all(datetime.fromisoformat(run).hour == 3 for run in body["next_runs"])
    bad = client.get("/api/schedules/preview", params={"cron": "0 3 * *"})
    assert bad.status_code == 422
    assert bad.json()["detail"] == "要 5 段（分 时 日 月 周），这里是 4 段"


def test_startup_runs_missed_schedule_once(tmp_path: Path, folder: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data")
    app = create_app(settings, start_worker=False)
    with TestClient(app, base_url=LOCAL) as client:
        library_id = client.post(
            "/api/libraries",
            json={
                "name": "电影",
                "data_type_id": "movie",
                "paths": [str(folder)],
                "automation": "schedule",
            },
        ).json()["id"]
    with app.state.session_factory() as session:
        row = session.scalars(select(Schedule)).one()
        row.next_run_at = stamp(datetime.now(UTC) - timedelta(days=2))
        session.commit()

    again = create_app(settings, start_worker=False, start_automation=True, schedule_interval=60)
    with TestClient(again, base_url=LOCAL):
        pass
    with TestClient(again, base_url=LOCAL):
        pass
    with again.state.session_factory() as session:
        tasks = list(session.scalars(select(Task)))
        row = session.scalars(select(Schedule)).one()
        assert [json.loads(task.payload or "")["library_id"] for task in tasks] == [library_id]
        assert row.next_run_at is not None and row.next_run_at > stamp(datetime.now(UTC))
