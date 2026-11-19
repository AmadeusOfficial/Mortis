"""示例片长来源。`api.example.com` 是编出来的地址，只演示写法。"""

from typing import Any

from mortis.plugin_api import Hit, Host, SourceError, SourcePlugin

API = "https://api.example.com/films"


class FilmSource(SourcePlugin):
    id = "example.tag_rules_films"
    name = "示例片长来源"
    data_type = "movie"
    provider = "example_films"

    def search(self, host: Host, title: str) -> list[Hit]:
        data = host.get_json(f"{API}/search", params={"title": title})
        return [
            Hit(external_id=str(row["id"]), title=row["title"], year=row.get("year"))
            for row in data.get("films", [])
        ]

    def fetch(self, host: Host, external_id: str) -> dict[str, Any]:
        data = host.get_json(f"{API}/{external_id}")
        if not isinstance(data, dict) or "title" not in data:
            raise SourceError("示例片长来源里没有这一条")
        return data

    def candidates(self, raw: dict[str, Any]) -> dict[str, Any]:
        minutes = raw.get("minutes")
        if isinstance(minutes, int) and not isinstance(minutes, bool) and minutes > 0:
            return {"runtime": minutes}
        return {}
