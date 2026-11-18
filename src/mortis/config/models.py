import re
from ipaddress import AddressValueError, IPv6Address
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from pydantic_core import PydanticCustomError

from mortis.rename.template import render

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

    proxy_host: str = ""
    proxy_port: int = Field(default=7890, ge=1, le=65535)
    ignore_cert_errors: bool = False

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
StillSize = Literal["w185", "w300", "original"]


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
    banner: bool = True
    clearart: bool = True
    discart: bool = True
    thumb: bool = True
    extrafanart: bool = False
    extrafanart_count: int = Field(default=5, ge=1, le=20)
    collection_poster: bool = True
    collection_fanart: bool = True
    actor_images: bool = True


# Windows 文件名里不允许的字符，模板和替换字符共用。
_FORBIDDEN_NAME_CHARS = frozenset('/\\:*?"<>|')


def _clean_name_template(value: str, tokens: dict[str, str]) -> str:
    """模板不能为空，不能带路径分隔符，并且必须能按给定名字渲染出来。"""
    template = value.strip()
    if not template:
        raise PydanticCustomError("template", "模板不能为空")
    if "/" in template or "\\" in template:
        raise PydanticCustomError("template", "模板里不能有路径分隔符")
    try:
        render(template, tokens, space="", colon="-")
    except ValueError as error:
        raise PydanticCustomError("template", str(error)) from None
    return template


def _clean_replacement(value: str) -> str:
    if len(value) > 3 or any(char in _FORBIDDEN_NAME_CHARS for char in value):
        raise PydanticCustomError("replacement", "替换字符不能是文件名里不允许的字符")
    return value


def _clean_skip_folders(value: list[str]) -> list[str]:
    """去掉首尾空白和空名，并按大小写不敏感去重，保留第一次出现的写法。"""
    cleaned: list[str] = []
    for raw in value:
        name = raw.strip()
        if name and name.casefold() not in {item.casefold() for item in cleaned}:
            cleaned.append(name)
    return cleaned


def validation_problems(error: ValidationError) -> str:
    """只列字段位置和原因，不带输入值，免得密钥写进错误消息。"""
    return "；".join(
        f"{'.'.join(str(part) for part in item['loc'])}：{item['msg']}"
        for item in error.errors(include_input=False, include_url=False)
    )


class MovieOutputConfig(BaseModel):
    """整理：按模板改名，并把 NFO 和图片写到视频旁边。"""

    model_config = ConfigDict(extra="ignore")

    organize_after_scrape: bool = True
    organize_rename: bool = False
    organize_write_files: bool = True
    nfo_names: list[Literal["movie", "filename"]] = Field(
        default_factory=lambda: ["movie", "filename"]
    )
    image_style: Literal["filename", "plain"] = "filename"
    collection_image_names: bool = True
    actor_images_folder: str = ".actors"
    folder_template: str = "${title} ${- ,edition,} (${year})"
    file_template: str = "${title}${- ,edition,}.${videoFormat}.${audioCodec}"
    folder_space: str = ""
    file_space: str = "."
    colon_replacement: str = "-"
    first_char_number: str = "#"
    nfo_write_lockdata: bool = False
    nfo_single_studio: bool = False

    @model_validator(mode="before")
    @classmethod
    def _from_old_switches(cls, data: Any) -> Any:
        """旧版的「刮削后写出」「刮削后重命名」换算成整理的三个开关。"""
        if not isinstance(data, dict) or not (
            {"write_after_scrape", "rename_after_scrape"} & data.keys()
        ):
            return data
        data = dict(data)
        write = bool(data.pop("write_after_scrape", True))
        rename = bool(data.pop("rename_after_scrape", False))
        data.setdefault("organize_after_scrape", write or rename)
        data.setdefault("organize_rename", rename)
        data.setdefault("organize_write_files", write or not rename)
        return data

    @field_validator("nfo_names")
    @classmethod
    def _clean_nfo_names(
        cls, value: list[Literal["movie", "filename"]]
    ) -> list[Literal["movie", "filename"]]:
        cleaned: list[Literal["movie", "filename"]] = []
        for name in value:
            if name not in cleaned:
                cleaned.append(name)
        if not cleaned:
            raise PydanticCustomError("nfo_names", "NFO 文件名至少选一个")
        return cleaned

    @field_validator("actor_images_folder")
    @classmethod
    def _check_actor_images_folder(cls, value: str) -> str:
        folder = value.strip()
        if not folder or any(char in _FORBIDDEN_NAME_CHARS for char in folder):
            raise PydanticCustomError("actor_images_folder", "演员头像目录只填一个目录名")
        return folder

    @field_validator("folder_template", "file_template")
    @classmethod
    def _check_template(cls, value: str) -> str:
        return _clean_name_template(value, {"title": "片名", "year": "2000"})

    @field_validator("folder_space", "file_space", "colon_replacement", "first_char_number")
    @classmethod
    def _check_replacement(cls, value: str) -> str:
        return _clean_replacement(value)


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
    detect_aspect_ratio: bool = False
    """宽高比检测还没实现，先占位；本机 TMM 也是关的。"""
    images: MovieImages = Field(default_factory=MovieImages)
    output: MovieOutputConfig = Field(default_factory=MovieOutputConfig)

    @field_validator("skip_folders")
    @classmethod
    def _clean_skip_folders(cls, value: list[str]) -> list[str]:
        return _clean_skip_folders(value)


class TvImages(BaseModel):
    """刮削电视剧时抓哪些图。默认照本机 TMM：图片全抓，演员头像不写。"""

    model_config = ConfigDict(extra="ignore")

    poster: bool = True
    fanart: bool = True
    clearlogo: bool = True
    keyart: bool = True
    banner: bool = True
    clearart: bool = True
    thumb: bool = True
    characterart: bool = True
    season_poster: bool = True
    season_banner: bool = True
    season_thumb: bool = True
    episode_thumb: bool = True
    actor_images: bool = False


class TvOutputConfig(BaseModel):
    """整理：按模板改名，并把 NFO 和图片写到视频旁边。默认照本机 TMM 的电视剧设置。"""

    model_config = ConfigDict(extra="ignore")

    organize_after_scrape: bool = True
    organize_rename: bool = True
    organize_write_files: bool = True
    show_template: str = "${showTitle} (${showYear})"
    season_template: str = "Season ${seasonNr}"
    file_template: str = "${showTitle}.S${seasonNr2}E${episodeNr2}.${title}"
    show_space: str = ""
    season_space: str = ""
    file_space: str = "."
    colon_replacement: str = " "
    first_char_number: str = "#"
    nfo_write_lockdata: bool = False
    nfo_single_studio: bool = False

    @field_validator("show_template", "season_template", "file_template")
    @classmethod
    def _check_template(cls, value: str) -> str:
        return _clean_name_template(
            value,
            {
                "showTitle": "剧名",
                "showYear": "2000",
                "seasonNr": "1",
                "seasonNr2": "01",
                "episodeNr": "1",
                "episodeNr2": "01",
                "title": "集名",
            },
        )

    @field_validator("show_space", "season_space", "file_space", "colon_replacement")
    @classmethod
    def _check_replacement(cls, value: str) -> str:
        return _clean_replacement(value)


class TvScrapeConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    language: str = Field(default="zh-CN", pattern=r"^[a-z]{2}-[A-Z]{2}$")
    certification_country: str = Field(default="US", pattern=r"^[A-Z]{2}$")
    include_adult: bool = True
    match_threshold: float = Field(default=0.75, ge=0.0, le=1.0)
    skip_folders: list[str] = Field(default_factory=lambda: ["MAKEMKV"])
    poster_size: PosterSize = "w780"
    fanart_size: FanartSize = "original"
    still_size: StillSize = "w300"
    prefer_textless_fanart: bool = True
    detect_aspect_ratio: bool = False
    """宽高比检测还没实现，先占位；本机 TMM 也是关的。"""
    show_missing_episodes: bool = True
    show_missing_specials: bool = True
    images: TvImages = Field(default_factory=TvImages)
    output: TvOutputConfig = Field(default_factory=TvOutputConfig)

    @field_validator("skip_folders")
    @classmethod
    def _clean_skip_folders(cls, value: list[str]) -> list[str]:
        return _clean_skip_folders(value)


class TasksConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    concurrency: int = Field(default=3, ge=1, le=8)
    keep_days: int = Field(default=30, ge=1, le=3650)


class PluginTypeConfig(BaseModel):
    """插件数据类型的类型级设置：识别阈值和扫描时跳过的目录。"""

    model_config = ConfigDict(extra="ignore")

    match_threshold: float = Field(default=0.75, ge=0.0, le=1.0)
    skip_folders: list[str] = Field(default_factory=list)

    @field_validator("skip_folders")
    @classmethod
    def _clean_skip_folders(cls, value: list[str]) -> list[str]:
        return _clean_skip_folders(value)


type BuiltinTypeSection = MovieScrapeConfig | TvScrapeConfig
type TypeSection = BuiltinTypeSection | PluginTypeConfig

BUILTIN_TYPE_SECTIONS: dict[str, type[BuiltinTypeSection]] = {
    "movie": MovieScrapeConfig,
    "tv": TvScrapeConfig,
    "anime": TvScrapeConfig,
}
"""内置数据类型各用哪种设置结构。其余类型都是插件类型，用 PluginTypeConfig。"""


class TypesConfig(BaseModel):
    """每种数据类型一节：内置三种是固定字段，插件类型的额外键按 PluginTypeConfig 校验。"""

    model_config = ConfigDict(extra="allow")

    movie: MovieScrapeConfig = Field(default_factory=MovieScrapeConfig)
    tv: TvScrapeConfig = Field(default_factory=TvScrapeConfig)
    anime: TvScrapeConfig = Field(default_factory=TvScrapeConfig)

    @model_validator(mode="before")
    @classmethod
    def _plugin_sections(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        cleaned = dict(data)
        for key, value in cleaned.items():
            if key in cls.model_fields or isinstance(value, PluginTypeConfig):
                continue
            try:
                cleaned[key] = PluginTypeConfig.model_validate(value)
            except ValidationError as error:
                raise PydanticCustomError(
                    "plugin_type_section",
                    "{name} 的设置不合法：{problems}",
                    {"name": key, "problems": validation_problems(error)},
                ) from None
        return cleaned

    def plugin(self, data_type_id: str) -> PluginTypeConfig:
        """插件类型的设置；没写过时是全默认值。"""
        value = (self.model_extra or {}).get(data_type_id)
        return value if isinstance(value, PluginTypeConfig) else PluginTypeConfig()


class PluginsConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    disabled: list[str] = []


class PlaybackConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    player: str = Field(default="", pattern=r"^([a-z][a-z0-9_]*)?$")
    """播放插件 id；空字符串表示用第一个可用的播放插件。"""


class AiConfig(BaseModel):
    """智能体用的模型接口：OpenAI 兼容的 Chat Completions。key 在密钥文件的 [ai]。"""

    model_config = ConfigDict(extra="ignore")

    base_url: str = "https://api.deepseek.com"
    """请求发到 {base_url}/chat/completions。"""
    model: str = "deepseek-flash"
    max_rounds: int = Field(default=8, ge=1, le=20)
    """一次提问最多几轮工具调用。"""

    @field_validator("base_url")
    @classmethod
    def _check_base_url(cls, value: str) -> str:
        url = value.strip().rstrip("/")
        if not re.fullmatch(r"https?://[^\s/]+(/\S*)?", url):
            raise PydanticCustomError("base_url", "接口地址要以 http:// 或 https:// 开头")
        return url

    @field_validator("model")
    @classmethod
    def _check_model(cls, value: str) -> str:
        model = value.strip()
        if not model:
            raise PydanticCustomError("model", "模型名不能为空")
        return model


class AppConfig(BaseModel):
    """普通设置。每种数据类型的设置在 types 下，插件自己的设置和密钥都在 sources 下。"""

    model_config = ConfigDict(extra="ignore")

    network: NetworkConfig = Field(default_factory=NetworkConfig)
    files: FilesConfig = Field(default_factory=FilesConfig)
    tasks: TasksConfig = Field(default_factory=TasksConfig)
    types: TypesConfig = Field(default_factory=TypesConfig)
    sources: dict[str, dict[str, Any]] = {}
    """每个来源或插件一节，按插件自己的 Settings 模型校验。"""
    plugins: PluginsConfig = Field(default_factory=PluginsConfig)
    playback: PlaybackConfig = Field(default_factory=PlaybackConfig)
    ai: AiConfig = Field(default_factory=AiConfig)


CONFIG_SECTIONS: dict[str, type[BaseModel]] = {
    "network": NetworkConfig,
    "files": FilesConfig,
    "tasks": TasksConfig,
    "playback": PlaybackConfig,
    "ai": AiConfig,
}

TYPES_PREFIX = "types."
"""save_section 里代表 types 下一节的键名前缀，例如 types.galgame。"""

TMDB_SOURCE = "tmdb"


class NetworkSecrets(BaseModel):
    model_config = ConfigDict(extra="ignore")

    proxy_username: str = ""
    proxy_password: str = ""


class AiSecrets(BaseModel):
    model_config = ConfigDict(extra="ignore")

    api_key: str = ""


class Secrets(BaseModel):
    """密钥。代理凭据在 network 下，模型接口的 key 在 ai 下，来源和插件的密钥在 sources.<id> 下。"""

    model_config = ConfigDict(extra="ignore")

    network: NetworkSecrets = Field(default_factory=NetworkSecrets)
    sources: dict[str, dict[str, Any]] = {}
    ai: AiSecrets = Field(default_factory=AiSecrets)

    @property
    def tmdb_api_key(self) -> str:
        values = self.sources.get(TMDB_SOURCE)
        key = values.get("api_key") if isinstance(values, dict) else None
        return key if isinstance(key, str) else ""


SECRET_KEYS: tuple[str, ...] = ("tmdb_api_key", "proxy_username", "proxy_password", "ai_api_key")
"""设置页能改的四个密钥，形状固定；它们分别存在 network、sources.tmdb 和 ai 下。"""


def secret_value(secrets: Secrets, key: str) -> str:
    """按设置页的键名读密钥，不关心存在哪一节。"""
    if key == "tmdb_api_key":
        return secrets.tmdb_api_key
    if key == "proxy_username":
        return secrets.network.proxy_username
    if key == "proxy_password":
        return secrets.network.proxy_password
    if key == "ai_api_key":
        return secrets.ai.api_key
    raise KeyError(key)


def set_secret(secrets: Secrets, key: str, value: str) -> None:
    """按设置页的键名写密钥，保留同一节里其他插件的键。"""
    if key == "tmdb_api_key":
        section = secrets.sources.get(TMDB_SOURCE)
        values = dict(section) if isinstance(section, dict) else {}
        values["api_key"] = value
        secrets.sources[TMDB_SOURCE] = values
        return
    if key == "proxy_username":
        secrets.network.proxy_username = value
        return
    if key == "proxy_password":
        secrets.network.proxy_password = value
        return
    if key == "ai_api_key":
        secrets.ai.api_key = value
        return
    raise KeyError(key)
