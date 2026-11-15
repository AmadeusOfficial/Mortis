from pathlib import Path

import pytest
from fastapi import FastAPI

from mortis import server


def test_main_creates_data_dir_and_runs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    data_dir = tmp_path / "nested" / "data"
    monkeypatch.setenv("MORTIS_DATA_DIR", str(data_dir))
    monkeypatch.setenv("MORTIS_PORT", "18123")
    monkeypatch.delenv("MORTIS_WEB_DIST", raising=False)
    calls: list[tuple[object, str, int]] = []

    def fake_run(app: object, host: str, port: int) -> None:
        calls.append((app, host, port))

    monkeypatch.setattr(server.uvicorn, "run", fake_run)
    server.main()

    assert data_dir.is_dir()
    assert len(calls) == 1
    app, host, port = calls[0]
    assert isinstance(app, FastAPI)
    assert (host, port) == ("127.0.0.1", 18123)
