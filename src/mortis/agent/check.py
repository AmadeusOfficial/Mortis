import httpx2 as httpx
from pydantic import BaseModel

from mortis.config.models import AiConfig

REASON_LIMIT = 200


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
    if response.status_code in (401, 403):
        return AiCheck(ok=False, message=f"key 不对：接口返回 HTTP {response.status_code}")
    if response.status_code != 200:
        reason = scrub(error_reason(response), key)
        suffix = f"：{reason}" if reason else ""
        return AiCheck(ok=False, message=f"接口返回 HTTP {response.status_code}{suffix}")
    if not _has_choices(response):
        return AiCheck(ok=False, message="接口返回的不是 Chat Completions 的结果，检查接口地址")
    return AiCheck(ok=True, message=f"连接成功，模型 {config.model} 可用")


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


def scrub(text: str, key: str) -> str:
    return text.replace(key, "***") if key else text
