import os
import re
import tempfile
from hashlib import sha256
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse

import httpx2 as httpx

IMAGE_DIR = "images"
URL_INDEX = "urls"
"""图片目录下按地址记下存放路径。TMDB 的图片地址和内容一一对应，下过的不再下载。"""
EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".webp", ".svg"})
_RELATIVE = re.compile(r"[0-9a-f]{2}/[0-9a-f]{64}\.(?:jpg|jpeg|png|webp|svg)")


class ImageError(Exception):
    pass


class ImageStore:
    """按内容哈希存图，同一张图只存一份。返回的相对路径写进库里。"""

    def __init__(self, data_dir: Path) -> None:
        self.root = data_dir / IMAGE_DIR

    def save(self, content: bytes, ext: str) -> str:
        digest = sha256(content).hexdigest()
        rel = f"{digest[:2]}/{digest}{ext}"
        path = self.root / rel
        if not path.is_file():
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, temp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(content)
                os.replace(temp, path)
            except BaseException:
                Path(temp).unlink(missing_ok=True)
                raise
        return rel

    def _index(self, url: str) -> Path:
        digest = sha256(url.encode()).hexdigest()
        return self.root / URL_INDEX / digest[:2] / digest

    def cached(self, url: str) -> str | None:
        """这个地址以前下过、文件还在时返回存放路径。"""
        index = self._index(url)
        if not index.is_file():
            return None
        rel = index.read_text(encoding="utf-8").strip()
        return rel if self.resolve(rel) is not None else None

    def download(self, http: httpx.Client, url: str) -> str:
        try:
            response = http.get(url)
        except httpx.HTTPError as error:
            raise ImageError(type(error).__name__) from None
        if response.status_code != 200:
            raise ImageError(f"HTTP {response.status_code}")
        if not response.headers.get("content-type", "").startswith("image/"):
            raise ImageError("返回的不是图片")
        ext = PurePosixPath(urlparse(url).path).suffix.lower()
        rel = self.save(response.content, ext if ext in EXTENSIONS else ".jpg")
        index = self._index(url)
        index.parent.mkdir(parents=True, exist_ok=True)
        index.write_text(rel, encoding="utf-8")
        return rel

    def resolve(self, rel: str | None) -> Path | None:
        if not rel or not _RELATIVE.fullmatch(rel):
            return None
        path = self.root / rel
        return path if path.is_file() else None
