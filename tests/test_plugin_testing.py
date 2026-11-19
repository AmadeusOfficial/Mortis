import logging

import pytest
from pydantic import BaseModel

from mortis.plugin_api import Host, PlayerError, PlayerHost
from mortis.plugin_api.testing import FakeHookHost, FakeHost, FakePlayerHost, FakeRequest


class Opts(BaseModel):
    limit: int = 3


def test_fake_host_returns_registered_responses_and_records_requests() -> None:
    host = FakeHost(
        settings=Opts(limit=5),
        responses={
            "https://x.test/a": {"ok": True},
            "https://x.test/b": lambda request: {"echo": request.json},
            "https://x.test/t": "纯文本",
            "https://x.test/bad": RuntimeError("坏了"),
        },
        token="t0",
    )
    assert host.get_json("https://x.test/a", params={"q": "1"}) == {"ok": True}
    assert host.post_json("https://x.test/b", json={"k": 1}) == {"echo": {"k": 1}}
    assert host.get_text("https://x.test/t") == "纯文本"
    with pytest.raises(RuntimeError):
        host.get_json("https://x.test/bad")
    assert host.access_token() == "t0"
    host.log("hello")
    assert host.logs == ["hello"]
    assert host.requests[0] == FakeRequest("GET", "https://x.test/a", params={"q": "1"})
    assert [r.method for r in host.requests] == ["GET", "POST", "GET", "GET"]
    assert host.settings.limit == 5  # type: ignore[attr-defined]


def test_fake_host_rejects_unregistered_requests() -> None:
    with pytest.raises(AssertionError, match="没登记"):
        FakeHost().get_json("https://x.test/none")


def test_fakes_satisfy_the_host_protocols() -> None:
    hosts: list[Host] = [FakeHost()]
    players: list[PlayerHost] = [FakePlayerHost()]
    assert hosts and players


def test_fake_player_host_records_launches_and_failures() -> None:
    host = FakePlayerHost(fail={"bad.exe"})
    host.launch("a.exe", ["-x"], "D:/g")
    host.open_default("D:/v.mkv")
    assert host.launched == [("a.exe", ["-x"], "D:/g")]
    assert host.opened == ["D:/v.mkv"]
    with pytest.raises(PlayerError):
        host.launch("bad.exe", [])


def test_fake_hook_host_is_a_minimal_placeholder() -> None:
    host = FakeHookHost()
    host.log("x")
    assert host.logs == ["x"]


def test_plugin_log_messages_reach_backend_logger(
    tmp_path, caplog: pytest.LogCaptureFixture
) -> None:
    from mortis.api.routes.plugins import _log_plugin_message

    with caplog.at_level(logging.INFO, logger="mortis.plugins"):
        _log_plugin_message("[来源] GET https://x.test")
    assert "GET https://x.test" in caplog.text
