import re
from ipaddress import AddressValueError, IPv6Address
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_core import PydanticCustomError

PROXY_HOST_MESSAGE = "代理主机只填主机名或 IP，不要带 http:// 或端口；IPv6 要加方括号"
_FORBIDDEN_IN_HOST = re.compile(r"://|[/@\s]")


def _is_plain_host(host: str) -> bool:
    """主机名、IPv4 或带方括号的 IPv6；协议、端口、账号、路径都不收。"""
    if _FORBIDDEN_IN_HOST.search(host):
        return False
    if host.startswith("[") and host.endswith("]"):
        try:
            IPv6Address(host[1:-1])
        except AddressValueError:
            return False
        return True
    return ":" not in host and "[" not in host and "]" not in host


class NetworkConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    proxy_host: str = "127.0.0.1"
    proxy_port: int = Field(default=7890, ge=1, le=65535)
    ignore_cert_errors: bool = True

    @field_validator("proxy_host")
    @classmethod
    def _check_host(cls, value: str) -> str:
        host = value.strip()
        if host and not _is_plain_host(host):
            raise PydanticCustomError("proxy_host", PROXY_HOST_MESSAGE)
        return host


# TMM 5 默认的视频和字幕扩展名。
DEFAULT_VIDEO_EXTENSIONS = [
    ".3gp",
    ".asf",
    ".asx",
    ".avc",
    ".avi",
    ".bdmv",
    ".bin",
    ".bivx",
    ".braw",
    ".dat",
    ".divx",
    ".dv",
    ".dvr-ms",
    ".disc",
    ".evo",
    ".f4v",
    ".fli",
    ".flv",
    ".h264",
    ".ifo",
    ".img",
    ".iso",
    ".mts",
    ".mt2s",
    ".m2ts",
    ".m2v",
    ".m4v",
    ".mkv",
    ".mk3d",
    ".mov",
    ".mp4",
    ".mpeg",
    ".mpg",
    ".nrg",
    ".nsv",
    ".nuv",
    ".ogm",
    ".ogv",
    ".pva",
    ".qt",
    ".rm",
    ".rmvb",
    ".strm",
    ".svq3",
    ".ts",
    ".ty",
    ".viv",
    ".vob",
    ".vp3",
    ".wmv",
    ".webm",
    ".xvid",
]
DEFAULT_SUBTITLE_EXTENSIONS = [
    ".aqt",
    ".ass",
    ".cvd",
    ".dks",
    ".idx",
    ".jss",
    ".mpl",
    ".pjs",
    ".psb",
    ".rt",
    ".smi",
    ".srt",
    ".ssa",
    ".ssf",
    ".sub",
    ".sup",
    ".svcd",
    ".ttxt",
    ".usf",
    ".vtt",
]
_EXTENSION = re.compile(r"\.[a-z0-9][a-z0-9-]*")

PosterSize = Literal["w342", "w500", "w780", "original"]
FanartSize = Literal["w780", "w1280", "original"]


def _clean_extensions(values: list[str]) -> list[str]:
    cleaned: list[str] = []
    for raw in values:
        ext = raw.strip().lower()
        if not ext:
            continue
        if not ext.startswith("."):
            ext = "." + ext
        if not _EXTENSION.fullmatch(ext):
            raise PydanticCustomError("extension", "扩展名只能含字母、数字和连字符")
        if ext not in cleaned:
            cleaned.append(ext)
    if not cleaned:
        raise PydanticCustomError("extension", "扩展名名单不能为空")
    return cleaned


class FilesConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    video_extensions: list[str] = Field(default_factory=lambda: list(DEFAULT_VIDEO_EXTENSIONS))
    subtitle_extensions: list[str] = Field(
        default_factory=lambda: list(DEFAULT_SUBTITLE_EXTENSIONS)
    )

    @field_validator("video_extensions", "subtitle_extensions")
    @classmethod
    def _check_extensions(cls, value: list[str]) -> list[str]:
        return _clean_extensions(value)


class MovieImages(BaseModel):
    """刮削时抓哪些图。和 TMM 一样默认全开。"""

    model_config = ConfigDict(extra="ignore")

    poster: bool = True
    fanart: bool = True
    clearlogo: bool = True
    keyart: bool = True
    collection_poster: bool = True
    collection_fanart: bool = True
    actor_images: bool = True


class MovieScrapeConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    language: str = Field(default="zh-CN", pattern=r"^[a-z]{2}-[A-Z]{2}$")
    certification_country: str = Field(default="US", pattern=r"^[A-Z]{2}$")
    include_adult: bool = True
    match_threshold: float = Field(default=0.75, ge=0.0, le=1.0)
    skip_folders: list[str] = Field(default_factory=lambda: ["MAKEMKV"])
    poster_size: PosterSize = "w780"
    fanart_size: FanartSize = "original"
    prefer_textless_fanart: bool = True
    images: MovieImages = Field(default_factory=MovieImages)

    @field_validator("skip_folders")
    @classmethod
    def _clean_skip_folders(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        for raw in value:
            name = raw.strip()
            if name and name.casefold() not in {item.casefold() for item in cleaned}:
                cleaned.append(name)
        return cleaned


class TasksConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    concurrency: int = Field(default=3, ge=1, le=8)
    keep_days: int = Field(default=30, ge=1, le=3650)


class AppConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    network: NetworkConfig = Field(default_factory=NetworkConfig)
    files: FilesConfig = Field(default_factory=FilesConfig)
    movie: MovieScrapeConfig = Field(default_factory=MovieScrapeConfig)
    tasks: TasksConfig = Field(default_factory=TasksConfig)


CONFIG_SECTIONS: dict[str, type[BaseModel]] = {
    "network": NetworkConfig,
    "files": FilesConfig,
    "movie": MovieScrapeConfig,
    "tasks": TasksConfig,
}


class Secrets(BaseModel):
    model_config = ConfigDict(extra="ignore")

    tmdb_api_key: str = ""
    proxy_username: str = ""
    proxy_password: str = ""


SECRET_KEYS: tuple[str, ...] = tuple(Secrets.model_fields)
