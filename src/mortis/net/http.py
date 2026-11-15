from urllib.parse import quote

import httpx2 as httpx

from mortis.config.models import NetworkConfig, Secrets
from mortis.version import get_version

TIMEOUT_SECONDS = 15.0


def proxy_url(network: NetworkConfig, secrets: Secrets) -> str | None:
    if not network.proxy_host:
        return None
    auth = ""
    if secrets.proxy_username:
        user = quote(secrets.proxy_username, safe="")
        password = quote(secrets.proxy_password, safe="")
        auth = f"{user}:{password}@"
    return f"http://{auth}{network.proxy_host}:{network.proxy_port}"


def build_client(
    network: NetworkConfig,
    secrets: Secrets,
    *,
    transport: httpx.BaseTransport | None = None,
) -> httpx.Client:
    return httpx.Client(
        # 设了代理时 httpx 会给所有地址挂代理传输，注入的测试传输就不生效了。
        proxy=None if transport is not None else proxy_url(network, secrets),
        verify=not network.ignore_cert_errors,
        timeout=TIMEOUT_SECONDS,
        headers={"User-Agent": f"Mortis/{get_version()}"},
        follow_redirects=True,
        trust_env=False,
        transport=transport,
    )
