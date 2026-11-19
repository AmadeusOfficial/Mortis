import json
from pathlib import Path

from fastapi.testclient import TestClient

from mortis.api.app import create_app
from mortis.db.models import Item, ItemImage, Library, MediaFile, utc_now
from mortis.settings import Settings


def _seed_tv(data_dir: Path, tmp_path: Path) -> tuple[Settings, dict[str, int]]:
    settings = Settings(data_dir=data_dir)
    (tmp_path / "anime").mkdir(parents=True, exist_ok=True)
    app = create_app(settings, start_worker=False)
    now = utc_now()
    with TestClient(app, base_url="http://127.0.0.1") as client:
        lib = client.post(
            "/api/libraries",
            json={"name": "动漫", "data_type_id": "anime", "paths": [str(tmp_path / "anime")]},
        ).json()
    with app.state.session_factory() as session:
        library = session.get(Library, lib["id"])
        assert library is not None
        show = Item(
            library_id=library.id,
            data_type_id="anime",
            kind="work",
            title="蜡笔小新",
            created_at=now,
            updated_at=now,
        )
        session.add(show)
        session.flush()
        session.add(
            ItemImage(
                item_id=show.id,
                image_kind="fanart",
                source="tmdb",
                file_path="show_fanart.jpg",
                position=0,
            )
        )

        s1 = Item(
            library_id=library.id,
            data_type_id="anime",
            parent_id=show.id,
            kind="season",
            index_number=1,
            title="第 1 季",
            overview="第一季简介",
            release_date="1992-04-13",
            created_at=now,
            updated_at=now,
        )
        s2 = Item(
            library_id=library.id,
            data_type_id="anime",
            parent_id=show.id,
            kind="season",
            index_number=2,
            title="第 2 季",
            created_at=now,
            updated_at=now,
        )
        session.add_all([s1, s2])
        session.flush()

        ep1 = Item(
            library_id=library.id,
            data_type_id="anime",
            parent_id=s1.id,
            kind="episode",
            index_number=1,
            title="帮妈妈买东西喔",
            overview="小新去买东西",
            release_date="1992-04-13",
            fields=json.dumps({"runtime": 25}),
            created_at=now,
            updated_at=now,
        )
        ep2 = Item(
            library_id=library.id,
            data_type_id="anime",
            parent_id=s1.id,
            kind="episode",
            index_number=2,
            title="三轮车很好玩喔",
            release_date="1992-04-20",
            created_at=now,
            updated_at=now,
        )
        s2_ep1 = Item(
            library_id=library.id,
            data_type_id="anime",
            parent_id=s2.id,
            kind="episode",
            index_number=1,
            title="吃年夜饭喔",
            release_date="1993-01-11",
            created_at=now,
            updated_at=now,
        )
        session.add_all([ep1, ep2, s2_ep1])
        session.flush()

        video = tmp_path / "anime" / "ep01.mkv"
        video.parent.mkdir(parents=True, exist_ok=True)
        video.write_bytes(b"v")
        session.add(
            MediaFile(
                library_id=library.id,
                item_id=ep1.id,
                path=str(video),
                file_kind="video",
                size=1024,
                created_at=now,
                updated_at=now,
            )
        )
        sub = tmp_path / "anime" / "ep01.ass"
        sub.write_bytes(b"s")
        session.add(
            MediaFile(
                library_id=library.id,
                item_id=ep1.id,
                path=str(sub),
                file_kind="subtitle",
                size=128,
                created_at=now,
                updated_at=now,
            )
        )
        session.commit()
        ids = {"show": show.id, "s1": s1.id, "s2": s2.id, "ep1": ep1.id}
    return settings, ids


def test_season_detail_includes_show_seasons_episode_meta_and_fanart_fallback(
    tmp_path: Path,
) -> None:
    settings, ids = _seed_tv(tmp_path / "data", tmp_path)
    app = create_app(settings, start_worker=False)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        show_res = client.get(f"/api/items/{ids['show']}").json()
        assert len(show_res["seasons"]) == 2
        ep1 = show_res["seasons"][0]["episodes"][0]
        assert ep1["overview"] == "小新去买东西"
        assert ep1["runtime"] == 25
        assert [f["file_kind"] for f in ep1["files"]] == ["video", "subtitle"]

        season_res = client.get(f"/api/items/{ids['s1']}").json()
        assert season_res["kind"] == "season"
        assert season_res["fanart_url"] is not None
        assert len(season_res["seasons"]) == 2
        assert season_res["seasons"][0]["id"] == ids["s1"]
        assert season_res["seasons"][0]["episodes"][0]["overview"] == "小新去买东西"

        # 关闭展示缺失剧集：全缺的第 2 季应当隐藏
        resp = client.put(
            f"/api/items/{ids['show']}/missing-episodes",
            json={"show_missing_episodes": False},
        )
        assert resp.status_code == 200
        show_hidden = client.get(f"/api/items/{ids['show']}").json()
        assert [s["id"] for s in show_hidden["seasons"]] == [ids["s1"]]
        assert len(show_hidden["seasons"][0]["episodes"]) == 1

        season_hidden = client.get(f"/api/items/{ids['s1']}").json()
        assert [s["id"] for s in season_hidden["seasons"]] == [ids["s1"]]
        assert season_hidden["show_missing_effective"] is False
