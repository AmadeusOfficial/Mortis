"""回收站：只有本地固定盘走 Shell，其余直接删；Shell 报错或中途取消时报错。全部只碰临时目录。"""

import ctypes
import sys
from pathlib import Path

import pytest

from mortis.files import trash as trash_module
from mortis.files.trash import DRIVE_FIXED, SystemTrash, TrashError
from tests.fakes import make_junction as junction

windows = pytest.mark.skipif(sys.platform != "win32", reason="只有 Windows 有回收站")


@pytest.fixture
def fixed(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """本地固定盘、不是 subst；记下查过的根目录。"""
    roots: list[str] = []
    monkeypatch.setattr(trash_module, "drive_type", lambda root: roots.append(root) or DRIVE_FIXED)
    monkeypatch.setattr(trash_module, "dos_device", lambda _drive: r"\Device\HarddiskVolume3")
    return roots


def test_double_null_terminated() -> None:
    assert trash_module.double_null(r"C:\a\b") == "C:\\a\\b\0\0"


def test_flags_warn_before_permanent_delete() -> None:
    assert trash_module.FLAGS & 0x4000
    assert trash_module.FLAGS & trash_module.FOF_ALLOWUNDO


def test_plain_strips_long_prefixes() -> None:
    assert trash_module.plain("\\\\?\\C:\\a") == "C:\\a"
    assert trash_module.plain("\\\\?\\UNC\\server\\share\\a") == "\\\\server\\share\\a"
    assert trash_module.plain("C:\\a") == "C:\\a"


@pytest.mark.skipif(sys.platform != "win32" or sys.maxsize < 2**32, reason="64 位 Windows 的布局")
def test_struct_layout_matches_shellapi() -> None:
    assert ctypes.sizeof(trash_module._FileOp) == 56


@windows
def test_local_temp_drive_has_trash(tmp_path: Path) -> None:
    assert trash_module.drive_type(str(tmp_path.anchor)) == DRIVE_FIXED
    assert SystemTrash().is_permanent(tmp_path / "a.mkv") is False


@windows
@pytest.mark.parametrize("kind", [0, 1, 2, 4, 5, 6])
def test_non_fixed_drives_are_permanent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: int
) -> None:
    monkeypatch.setattr(trash_module, "drive_type", lambda _root: kind)
    monkeypatch.setattr(trash_module, "dos_device", lambda _drive: r"\Device\HarddiskVolume3")
    assert SystemTrash().is_permanent(tmp_path / "a.mkv") is True


@windows
def test_subst_drive_is_permanent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fixed: list[str]
) -> None:
    monkeypatch.setattr(trash_module, "dos_device", lambda _drive: "\\??\\D:\\dev")
    assert SystemTrash().is_permanent(tmp_path / "a.mkv") is True


@windows
def test_subst_checked_on_given_drive_before_resolve(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fixed: list[str]
) -> None:
    asked: list[str] = []

    def device(drive: str) -> str:
        asked.append(drive)
        return "\\??\\D:\\dev" if drive.upper() == "X:" else r"\Device\HarddiskVolume3"

    monkeypatch.setattr(trash_module, "dos_device", device)
    monkeypatch.setattr(trash_module, "location", lambda _path: str(tmp_path / "a.mkv"))
    assert SystemTrash().is_permanent(Path("X:\\a.mkv")) is True
    assert asked == ["X:"]
    assert SystemTrash().is_permanent(tmp_path / "a.mkv") is False


@windows
def test_long_prefix_path_checks_plain_drive(tmp_path: Path, fixed: list[str]) -> None:
    target = Path("\\\\?\\" + str(tmp_path / "a.mkv"))
    assert SystemTrash().is_permanent(target) is False
    assert fixed == [tmp_path.drive + "\\"]


@windows
def test_unc_is_permanent(fixed: list[str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(trash_module, "location", lambda _path: "\\\\server\\share\\a.mkv")
    assert SystemTrash().is_permanent(Path("\\\\server\\share\\a.mkv")) is True
    assert fixed == []


def test_permanent_delete_skips_shell(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(trash_module, "drive_type", lambda _root: trash_module.DRIVE_REMOTE)
    monkeypatch.setattr(trash_module, "shell_delete", _no_shell)
    folder = tmp_path / "电影"
    (folder / "sub").mkdir(parents=True)
    (folder / "sub" / "a.mkv").write_bytes(b"x")
    single = tmp_path / "b.nfo"
    single.write_bytes(b"y")

    system = SystemTrash()
    assert system.is_permanent(folder) is True
    system.remove(folder)
    system.remove(single)
    assert not folder.exists()
    assert not single.exists()


@windows
def test_permanent_delete_keeps_junction_target(tmp_path: Path) -> None:
    outside = tmp_path / "库外"
    outside.mkdir()
    (outside / "保留.mkv").write_bytes(b"keep")
    folder = tmp_path / "电影"
    folder.mkdir()
    (folder / "a.mkv").write_bytes(b"x")
    junction(folder / "链接", outside)

    trash_module.remove_permanently(folder)
    assert not folder.exists()
    assert (outside / "保留.mkv").exists()


@windows
def test_local_drive_goes_through_shell(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fixed: list[str]
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(trash_module, "shell_delete", lambda path: calls.append(path) or (0, False))
    target = tmp_path / "a.mkv"
    target.write_bytes(b"x")

    with pytest.raises(TrashError):
        SystemTrash().remove(target)
    assert calls == [str(target)]
    assert target.exists()


@windows
@pytest.mark.parametrize("outcome", [(2, False), (0, True)])
def test_shell_failure_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fixed: list[str], outcome: tuple[int, bool]
) -> None:
    monkeypatch.setattr(trash_module, "shell_delete", lambda _path: outcome)
    target = tmp_path / "a.mkv"
    target.write_bytes(b"x")
    with pytest.raises(TrashError):
        SystemTrash().remove(target)


def _no_shell(_path: str) -> tuple[int, bool]:
    raise AssertionError("没有回收站的盘不该经过 Shell")
