from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from mortis.api.deps import ServicesDep, SessionDep
from mortis.api.routes.items import get_item
from mortis.db.models import Item, MediaFile
from mortis.errors import UserError
from mortis.items.matching import is_series
from mortis.plugin_api import PlayerError
from mortis.plugins.player import choose_player

router = APIRouter(prefix="/api")


class PlayRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file_id: int | None = None


class PlayCandidate(BaseModel):
    file_id: int
    path: str
    name: str
    size: int | None
    exists: bool


class PlayChoices(BaseModel):
    """多个视频时不猜，把候选交还调用方，由它带 file_id 再请求一次。"""

    detail: str
    candidates: list[PlayCandidate]


class PlayResult(BaseModel):
    player: str
    player_name: str
    file_id: int
    path: str


def playable_videos(session: Session, item: Item) -> list[MediaFile]:
    if item.kind == "collection":
        raise UserError("系列电影不能直接播放，打开其中一部再播放", status_code=400)
    if item.kind == "season":
        raise UserError("季不能直接播放，选一集再播放", status_code=400)
    if item.kind == "work" and is_series(session, item):
        raise UserError("剧不能直接播放，选一集再播放", status_code=400)
    return list(
        session.scalars(
            select(MediaFile)
            .where(MediaFile.item_id == item.id, MediaFile.file_kind == "video")
            .order_by(MediaFile.path, MediaFile.id)
        )
    )


def _candidate(row: MediaFile) -> PlayCandidate:
    path = Path(row.path)
    return PlayCandidate(
        file_id=row.id, path=row.path, name=path.name, size=row.size, exists=path.is_file()
    )


@router.post(
    "/items/{item_id}/play",
    response_model=PlayResult,
    responses={409: {"model": PlayChoices}},
)
def play_item(
    item_id: int, session: SessionDep, services: ServicesDep, body: PlayRequest | None = None
) -> PlayResult | JSONResponse:
    item = get_item(session, item_id)
    videos = playable_videos(session, item)
    if not videos:
        raise UserError("这个条目没有视频文件", status_code=404)
    file_id = body.file_id if body is not None else None
    if file_id is None:
        if len(videos) > 1:
            choices = PlayChoices(
                detail=f"有 {len(videos)} 个视频，选一个再播放",
                candidates=[_candidate(row) for row in videos],
            )
            return JSONResponse(status_code=409, content=choices.model_dump(mode="json"))
        target = videos[0]
    else:
        found = next((row for row in videos if row.id == file_id), None)
        if found is None:
            raise UserError("这个条目没有这个视频文件", status_code=404)
        target = found
    if not Path(target.path).is_file():
        raise UserError(f"文件不在了：{target.path}", status_code=404)
    plugin, host = choose_player(services.plugins, services.launcher, services.config_store)
    try:
        plugin.play(host, target.path)
    except PlayerError as error:
        raise UserError(f"{plugin.name} 没能打开：{error.message}", status_code=502) from None
    except Exception as error:
        raise UserError(
            f"{plugin.name} 出错：{type(error).__name__}: {error}", status_code=502
        ) from None
    return PlayResult(
        player=plugin.id, player_name=plugin.name, file_id=target.id, path=target.path
    )
