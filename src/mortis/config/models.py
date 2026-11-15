import re
from ipaddress import AddressValueError, IPv6Address

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


class AppConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    network: NetworkConfig = Field(default_factory=NetworkConfig)


class Secrets(BaseModel):
    model_config = ConfigDict(extra="ignore")

    tmdb_api_key: str = ""
    proxy_username: str = ""
    proxy_password: str = ""


SECRET_KEYS: tuple[str, ...] = tuple(Secrets.model_fields)
