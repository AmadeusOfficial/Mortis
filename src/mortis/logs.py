import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_DIR = "logs"
LOG_FILE = "mortis.log"
MAX_BYTES = 5 * 1024 * 1024
BACKUPS = 4
_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def setup_logging(data_dir: Path) -> Path:
    """日志写到数据目录的 logs\\mortis.log，按大小滚动，同时照旧打到控制台。"""
    folder = data_dir / LOG_DIR
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / LOG_FILE
    formatter = logging.Formatter(_FORMAT)
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, "_mortis", False):
            root.removeHandler(handler)
            handler.close()
    handlers: list[logging.Handler] = [
        RotatingFileHandler(path, maxBytes=MAX_BYTES, backupCount=BACKUPS, encoding="utf-8")
    ]
    if sys.stderr is not None:
        handlers.append(logging.StreamHandler(sys.stderr))
    for handler in handlers:
        handler.setFormatter(formatter)
        handler._mortis = True  # type: ignore[attr-defined]
        root.addHandler(handler)
    root.setLevel(logging.INFO)
    # 每个图片请求一行，写进文件没用。
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    return path
