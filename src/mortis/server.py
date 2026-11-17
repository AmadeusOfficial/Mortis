import logging
import socket
import sys

import uvicorn

from mortis.api.app import create_app
from mortis.logs import setup_logging
from mortis.settings import Settings

PORT_IN_USE = 3
"""端口被占时的退出码，托盘看到它就不再重启。"""

logger = logging.getLogger("mortis.server")


def port_in_use(host: str, port: int) -> bool:
    """Windows 上 uvicorn 能绑上已被占用的端口，请求会落到旧进程，所以先用独占方式试绑一次。"""
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as probe:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            probe.bind((host, port))
        except OSError:
            return True
    return False


def main() -> None:
    settings = Settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    setup_logging(settings.data_dir)
    if port_in_use(settings.host, settings.port):
        logger.error("端口 %s 已被占用，没有启动。换个端口或先关掉占用它的程序。", settings.port)
        sys.exit(PORT_IN_USE)
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port, log_config=None)


if __name__ == "__main__":
    main()
