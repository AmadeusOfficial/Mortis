"""最小的来源插件：给电影补一句标语和简介。

`api.example.com` 是编出来的地址，这个插件只演示写法，装上之后搜不到任何东西。
要接真的站点，把 `API` 和三个方法里的字段名换成对方的。

`PLUGIN = HelloSource()` 是单个对象的简写：对象的 id 就是 plugin.toml 的 id，
plugin.toml 里不用再列 `[[sources]]`。
"""

from typing import Any

from mortis.plugin_api import Hit, Host, SourceError, SourcePlugin

API = "https://api.example.com/hello"


class HelloSource(SourcePlugin):
    id = "example.hello_source"
    name = "示例来源"
    data_type = "movie"
    provider = "example_hello"
    requests_per_second = 1.0

    def search(self, host: Host, title: str) -> list[Hit]:
        data = host.get_json(f"{API}/search", params={"q": title})
        return [
            Hit(
                external_id=str(row["id"]),
                title=row["title"],
                original_title=row.get("original_title"),
                year=row.get("year"),
            )
            for row in data.get("results", [])
        ]

    def fetch(self, host: Host, external_id: str) -> dict[str, Any]:
        data = host.get_json(f"{API}/movies/{external_id}")
        if not isinstance(data, dict) or "title" not in data:
            raise SourceError("示例来源里没有这一条")
        return data

    def candidates(self, raw: dict[str, Any]) -> dict[str, Any]:
        """只从快照里取值，不联网。键必须是电影字段目录里有的。"""
        found: dict[str, Any] = {}
        if raw.get("tagline"):
            found["tagline"] = str(raw["tagline"])
        if raw.get("overview"):
            found["overview"] = str(raw["overview"])
        return found


PLUGIN = HelloSource()
