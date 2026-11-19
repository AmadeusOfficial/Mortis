"""钩子联网：拒绝本机和私网地址（含跳转之后和域名解析出来的地址），限制超时和响应大小。

做法：每一跳都先检查再请求，不让客户端自己跟随跳转。域名用本机解析的结果判断，
解析和实际连接之间仍有极短的窗口（DNS 重绑定），所以这是尽力而为的防护，不是沙箱。
"""

import ipaddress
import json
import socket
from collections.abc import Callable
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx2 as httpx

from mortis.plugin_api.hooks import HookError, HookLimitError, HookLimits

MAX_REDIRECTS = 3
BLOCKED_SUFFIXES = (".localhost", ".local", ".internal")
FAKE_IP_NET = ipaddress.ip_network("198.18.0.0/15")
"""Clash 等工具的 fake-ip 模式把域名解析到这一段，请求实际交给代理；只对域名放行，字面 IP 不放。"""

Resolver = Callable[[str], list[str]]


def resolve(host: str) -> list[str]:
    try:
        found = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except OSError:
        raise HookError(f"解析不了域名 {host}") from None
    return sorted({str(item[4][0]) for item in found})


def _forbidden(address: str, *, allow_fake: bool) -> bool:
    try:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return True
    ip = getattr(ip, "ipv4_mapped", None) or ip
    if allow_fake and ip in FAKE_IP_NET:
        return False
    return not ip.is_global


def check_url(url: str, resolver: Resolver | None = None) -> None:
    """地址不允许访问时抛 HookLimitError。"""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise HookLimitError("只能访问 http 或 https 地址")
    if parts.username is not None or parts.password is not None:
        raise HookLimitError("地址里不能带账号密码")
    try:
        host = (parts.hostname or "").rstrip(".").lower()
    except ValueError:
        host = ""
    if not host:
        raise HookLimitError("地址里没有主机名")
    if host == "localhost" or host.endswith(BLOCKED_SUFFIXES):
        raise HookLimitError("不能访问本机或内网地址")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        literal = False
    else:
        literal = True
    addresses = [host] if literal else (resolver or resolve)(host)
    if not addresses:
        raise HookError(f"解析不了域名 {host}")
    if any(_forbidden(address, allow_fake=not literal) for address in addresses):
        raise HookLimitError("不能访问本机或内网地址")


def _read_limited(response: httpx.Response, limit: int) -> bytes:
    declared = response.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit:
        raise HookLimitError(f"响应超过 {limit} 字节")
    body = bytearray()
    for chunk in response.iter_bytes():
        body.extend(chunk)
        if len(body) > limit:
            raise HookLimitError(f"响应超过 {limit} 字节")
    return bytes(body)


def fetch_json(
    client: httpx.Client,
    method: str,
    url: str,
    limits: HookLimits,
    *,
    resolver: Resolver | None = None,
    params: dict[str, Any] | None = None,
    json_body: Any = None,
    data: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
) -> Any:
    timeout = httpx.Timeout(
        connect=limits.connect_timeout,
        read=limits.read_timeout,
        write=limits.read_timeout,
        pool=limits.connect_timeout,
    )
    for _hop in range(MAX_REDIRECTS + 1):
        check_url(url, resolver)
        try:
            with client.stream(
                method,
                url,
                params=params,
                json=json_body,
                data=data,
                headers=headers,
                follow_redirects=False,
                timeout=timeout,
            ) as response:
                location = response.headers.get("location")
                if response.is_redirect and location:
                    previous = urlsplit(url)
                    url = urljoin(url, location)
                    params = None
                    if response.status_code in (301, 302, 303) and method != "GET":
                        method, json_body, data = "GET", None, None
                    if headers and urlsplit(url).netloc != previous.netloc:
                        headers = {
                            key: value
                            for key, value in headers.items()
                            if key.lower() not in ("authorization", "cookie")
                        }
                    continue
                if response.status_code >= 400:
                    raise HookError(f"对方返回 HTTP {response.status_code}")
                body = _read_limited(response, limits.response_bytes)
        except httpx.HTTPError as error:
            raise HookError(f"请求失败：{type(error).__name__}") from None
        try:
            return json.loads(body)
        except ValueError:
            raise HookError("对方返回的不是 JSON") from None
    raise HookLimitError(f"跳转超过 {MAX_REDIRECTS} 次")
