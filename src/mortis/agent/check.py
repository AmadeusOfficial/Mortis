import httpx2 as httpx
from pydantic import BaseModel

from mortis.config.models import AiConfig, Secrets

REASON_LIMIT = 200
MIN_SECRET = 3
"""太短的值（空、单个字母）不当密钥遮，否则会把正常文字遮得一塌糊涂。"""


class AiCheck(BaseModel):
    ok: bool
    message: str


def check_ai(client: httpx.Client, config: AiConfig, api_key: str) -> AiCheck:
    """发一句极短的非流式请求，只看接口和 key 能不能用。"""
    key = api_key.strip()
    if not key:
        return AiCheck(ok=False, message="还没有填 AI 的 key")
    try:
        response = client.post(
            f"{config.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={
                "model": config.model,
                "messages": [{"role": "user", "content": "ping"}],
                "max_tokens": 1,
                "stream": False,
            },
        )
    except httpx.TimeoutException:
        return AiCheck(ok=False, message="连不上 AI 接口：超时")
    except httpx.HTTPError as error:
        text = str(error) or type(error).__name__
        return AiCheck(ok=False, message=f"连不上 AI 接口：{scrub(text, key)}")
    if response.status_code != 200:
        reason = scrub(error_reason(response), key)
        return AiCheck(ok=False, message=status_message(response.status_code, reason))
    if not _has_choices(response):
        return AiCheck(ok=False, message="接口返回的不是 Chat Completions 的结果，检查接口地址")
    return AiCheck(ok=True, message=f"连接成功，模型 {config.model} 可用")


def status_message(status: int, reason: str) -> str:
    """模型接口非 200 时给用户看的话。连接检查和对话共用；reason 要先遮掉 key。"""
    suffix = f"：{reason}" if reason else ""
    if status in (401, 403):
        return f"key 不对：接口返回 HTTP {status}"
    if status == 402:
        return f"余额不足：接口返回 HTTP 402{suffix}"
    if status in (400, 422):
        return f"请求被拒绝：{reason or f'接口返回 HTTP {status}'}"
    if status == 404:
        return f"接口返回 HTTP 404{suffix}，检查接口地址"
    if status == 429:
        return f"请求太频繁或额度用完了：HTTP 429{suffix}"
    if status >= 500:
        return f"AI 接口出错：HTTP {status}{suffix}"
    return f"接口返回 HTTP {status}{suffix}"


def error_reason(response: httpx.Response) -> str:
    """兼容接口的错误一般是 {"error": {"message": ...}}；不是 JSON 时取正文开头。"""
    try:
        body = response.json()
    except ValueError:
        return response.text.strip()[:REASON_LIMIT]
    return message_of(body)


def message_of(body: object) -> str:
    error = body.get("error") if isinstance(body, dict) else None
    if isinstance(error, dict) and isinstance(error.get("message"), str):
        return error["message"].strip()[:REASON_LIMIT]
    if isinstance(error, str):
        return error.strip()[:REASON_LIMIT]
    return ""


def _has_choices(response: httpx.Response) -> bool:
    try:
        body = response.json()
    except ValueError:
        return False
    return isinstance(body, dict) and isinstance(body.get("choices"), list)


def scrub(text: str, *keys: str) -> str:
    """把出现在文字里的密钥换成 ***。长的先换，免得短的密钥是长的一部分时漏一截。"""
    for key in sorted({key for key in keys if key}, key=len, reverse=True):
        text = text.replace(key, "***")
    return text


def secret_values(secrets: Secrets) -> tuple[str, ...]:
    """会出现在错误里的密钥：代理用户名和口令、TMDB key、AI key，以及插件的各项密钥。"""
    values = [
        secrets.network.proxy_username,
        secrets.network.proxy_password,
        secrets.tmdb_api_key,
        secrets.ai.api_key,
    ]
    for section in secrets.sources.values():
        if isinstance(section, dict):
            values.extend(value for value in section.values() if isinstance(value, str))
    return tuple(value.strip() for value in values if len(value.strip()) >= MIN_SECRET)
