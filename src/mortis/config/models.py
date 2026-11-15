from pydantic import BaseModel, ConfigDict, Field, field_validator


class NetworkConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    proxy_host: str = "127.0.0.1"
    proxy_port: int = Field(default=7890, ge=1, le=65535)
    ignore_cert_errors: bool = True

    @field_validator("proxy_host")
    @classmethod
    def _strip_host(cls, value: str) -> str:
        return value.strip()


class AppConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    network: NetworkConfig = Field(default_factory=NetworkConfig)


class Secrets(BaseModel):
    model_config = ConfigDict(extra="ignore")

    tmdb_api_key: str = ""
    proxy_username: str = ""
    proxy_password: str = ""


SECRET_KEYS: tuple[str, ...] = tuple(Secrets.model_fields)
