import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from mortis.settings import Settings, default_data_dir


@pytest.fixture(autouse=True)
def clean_mortis_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(os.environ):
        if key.upper().startswith("MORTIS_"):
            monkeypatch.delenv(key)


def test_default_data_dir_uses_localappdata(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert default_data_dir() == tmp_path / "Mortis"


def test_default_data_dir_without_localappdata(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    assert default_data_dir() == Path.home() / ".mortis"


def test_defaults(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for key in ("MORTIS_DATA_DIR", "MORTIS_HOST", "MORTIS_PORT", "MORTIS_WEB_DIST"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    settings = Settings()
    assert settings.data_dir == tmp_path / "Mortis"
    assert settings.host == "127.0.0.1"
    assert settings.port == 8100
    assert settings.web_dist is None


@pytest.mark.parametrize(
    ("key", "value", "field", "expected"),
    [
        ("MORTIS_DATA_DIR", r"E:\tools\Mortis\data", "data_dir", Path(r"E:\tools\Mortis\data")),
        ("MORTIS_HOST", "0.0.0.0", "host", "0.0.0.0"),
        ("MORTIS_PORT", "18100", "port", 18100),
        ("MORTIS_WEB_DIST", r"D:\web\dist", "web_dist", Path(r"D:\web\dist")),
    ],
)
def test_env_overrides(
    monkeypatch: pytest.MonkeyPatch, key: str, value: str, field: str, expected: object
) -> None:
    monkeypatch.setenv(key, value)
    assert getattr(Settings(), field) == expected


@pytest.mark.parametrize("value", ["", "   "])
def test_blank_web_dist_is_unset(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("MORTIS_WEB_DIST", value)
    assert Settings().web_dist is None


@pytest.mark.parametrize("value", ["", "   "])
def test_blank_data_dir_uses_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, value: str
) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("MORTIS_DATA_DIR", value)
    assert Settings().data_dir == tmp_path / "Mortis"


@pytest.mark.parametrize("value", ["abc", "0", "65536", "-1"])
def test_invalid_port_is_rejected(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("MORTIS_PORT", value)
    with pytest.raises(ValidationError) as info:
        Settings()
    assert "port" in str(info.value)
