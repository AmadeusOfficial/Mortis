import httpx2 as httpx
from pydantic import BaseModel

TMDB_API = "https://api.themoviedb.org/3"


class TmdbCheck(BaseModel):
    ok: bool
    message: str


def tmdb_auth(api_key: str) -> tuple[dict[str, str], dict[str, str]]:
    key = api_key.strip()
    if key.startswith("eyJ"):
        return {"Authorization": f"Bearer {key}"}, {}
    return {}, {"api_key": key}


def check_tmdb_key(client: httpx.Client, api_key: str) -> TmdbCheck:
    if not api_key.strip():
        return TmdbCheck(ok=False, message="还没有填 TMDB API Key")
    headers, params = tmdb_auth(api_key)
    try:
        response = client.get(f"{TMDB_API}/configuration", headers=headers, params=params)
    except httpx.HTTPError as error:
        # 异常文本可能带出请求地址（v3 的 key 在查询参数里），先抹掉 key。
        text = str(error) or type(error).__name__
        text = text.replace(api_key.strip(), "***")
        return TmdbCheck(ok=False, message=f"连不上 TMDB：{text}")
    if response.status_code == 200:
        return TmdbCheck(ok=True, message="连接成功")
    if response.status_code == 401:
        return TmdbCheck(ok=False, message="TMDB 拒绝了这个 API Key")
    return TmdbCheck(ok=False, message=f"TMDB 返回 HTTP {response.status_code}")
