import logging
import socket
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI

from mortis import logs, server


@pytest.fixture(autouse=True)
def restore_root_logger() -> Iterator[None]:
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    yield
    for handler in list(root.handlers):
        if handler not in handlers:
            root.removeHandler(handler)
            handler.close()
    root.setLevel(level)


@pytest.fixture
def server_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    data_dir = tmp_path / "nested" / "data"
    monkeypatch.setenv("MORTIS_DATA_DIR", str(data_dir))
    monkeypatch.delenv("MORTIS_WEB_DIST", raising=False)
    monkeypatch.delenv("MORTIS_HOST", raising=False)
    return data_dir


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def test_main_creates_data_dir_and_runs(monkeypatch: pytest.MonkeyPatch, server_env: Path) -> None:
    port = _free_port()
    monkeypatch.setenv("MORTIS_PORT", str(port))
    calls: list[tuple[object, str, int, object]] = []

    def fake_run(app: object, host: str, port: int, log_config: object) -> None:
        calls.append((app, host, port, log_config))

    monkeypatch.setattr(server.uvicorn, "run", fake_run)
    server.main()

    assert server_env.is_dir()
    assert (server_env / "logs" / "mortis.log").is_file()
    assert len(calls) == 1
    app, host, used_port, log_config = calls[0]
    assert isinstance(app, FastAPI)
    assert (host, used_port, log_config) == ("127.0.0.1", port, None)


def test_main_exits_when_port_taken(monkeypatch: pytest.MonkeyPatch, server_env: Path) -> None:
    with socket.socket() as taken:
        taken.bind(("127.0.0.1", 0))
        taken.listen()
        monkeypatch.setenv("MORTIS_PORT", str(taken.getsockname()[1]))
        monkeypatch.setattr(server.uvicorn, "run", lambda *a, **k: pytest.fail("不该启动"))
        with pytest.raises(SystemExit) as info:
            server.main()
    assert info.value.code == server.PORT_IN_USE
    log = (server_env / "logs" / "mortis.log").read_text(encoding="utf-8")
    assert "已被占用" in log


def test_port_in_use_detects_listener() -> None:
    with socket.socket() as taken:
        taken.bind(("127.0.0.1", 0))
        taken.listen()
        port = taken.getsockname()[1]
        assert server.port_in_use("127.0.0.1", port)
    assert not server.port_in_use("127.0.0.1", port)


def test_log_file_rotates(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(logs, "MAX_BYTES", 2000)
    path = logs.setup_logging(tmp_path)
    logs.setup_logging(tmp_path)
    mortis_handlers = [h for h in logging.getLogger().handlers if getattr(h, "_mortis", False)]
    assert len([h for h in mortis_handlers if isinstance(h, logging.FileHandler)]) == 1
    for index in range(200):
        logging.getLogger("mortis.test").warning("第 %s 行，凑够长度", index)
    assert path.is_file()
    assert (tmp_path / "logs" / "mortis.log.1").is_file()
    assert not (tmp_path / "logs" / "mortis.log.5").exists()
