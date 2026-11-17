"""打包后的冒烟检查：用临时数据目录和空闲端口起后端，查健康、版本、插件、前端和数据库。

用法：
  python scripts/smoke_package.py <Mortis.Server.exe> [--web-dist DIR] [--plugins DIR]
不给 --web-dist、--plugins 时用打包目录的默认位置（onedir 的上一级）。
"""

import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import tomllib
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_PLUGINS = 7
TIMEOUT = 60


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _get(url: str) -> tuple[int, bytes]:
    with urllib.request.urlopen(url, timeout=5) as response:
        return response.status, response.read()


def _json(url: str) -> object:
    return json.loads(_get(url)[1])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("server", type=Path)
    parser.add_argument("--web-dist", type=Path)
    parser.add_argument("--plugins", type=Path)
    args = parser.parse_args()
    expected = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"][
        "version"
    ]
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    with tempfile.TemporaryDirectory(prefix="mortis-smoke-") as data:
        env = {k: v for k, v in os.environ.items() if not k.startswith("MORTIS_")}
        env.update(MORTIS_DATA_DIR=data, MORTIS_PORT=str(port))
        if args.web_dist:
            env["MORTIS_WEB_DIST"] = str(args.web_dist)
        if args.plugins:
            env["MORTIS_PLUGINS_DIR"] = str(args.plugins)
        log = Path(data) / "smoke.out.log"
        with log.open("wb") as out:
            proc = subprocess.Popen([str(args.server)], env=env, stdout=out, stderr=out)
            try:
                return _check(base, expected, proc, Path(data))
            except Exception as error:
                print(f"失败：{error}")
                return 1
            finally:
                proc.terminate()
                try:
                    proc.wait(10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(10)
                if log.is_file():
                    tail = log.read_text(encoding="utf-8", errors="replace").splitlines()[-15:]
                    print("---- 后端输出最后几行 ----")
                    print("\n".join(tail))


def _check(base: str, expected: str, proc: subprocess.Popen[bytes], data: Path) -> int:
    deadline = time.monotonic() + TIMEOUT
    while True:
        if proc.poll() is not None:
            raise RuntimeError(f"后端提前退出，退出码 {proc.returncode}")
        try:
            if _get(f"{base}/api/health")[0] == 200:
                break
        except OSError:
            pass
        if time.monotonic() > deadline:
            raise RuntimeError(f"{TIMEOUT} 秒内没有就绪")
        time.sleep(0.5)
    started = TIMEOUT - (deadline - time.monotonic())
    print(f"就绪：{started:.1f} 秒")

    info = _json(f"{base}/api/system/info")
    assert isinstance(info, dict) and info["version"] == expected, f"版本不对：{info}"
    print(f"版本：{info['version']}")

    plugins = _json(f"{base}/api/plugins")
    assert isinstance(plugins, list)
    broken = [f"{p['id']}：{p['error']}" for p in plugins if p.get("error")]
    assert not broken, f"插件加载失败：{broken}"
    assert len(plugins) == EXPECTED_PLUGINS, f"插件数是 {len(plugins)}，应为 {EXPECTED_PLUGINS}"
    print(f"插件：{', '.join(p['id'] for p in plugins)}")

    status, body = _get(f"{base}/libraries")
    assert status == 200 and b'id="root"' in body, "前端页面没有返回"
    print("前端：/libraries 返回了页面")

    assert (data / "mortis.db").is_file(), "没有建数据库"
    assert (data / "logs" / "mortis.log").is_file(), "没有写日志文件"
    print("数据库和日志：已生成")
    print("冒烟通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
