import logging
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
from mortis.plugin_api import PlayerError, PlayerPlugin
from mortis.plugins.player import (
    PluginPlayerHost,
    choose_game_player,
    choose_player,
    remember_target,
    remembered_target,
)
from mortis.services import Services

router = APIRouter(prefix="/api")
logger = logging.getLogger("mortis.play")
BAD_TARGET = "选的程序不在游戏文件夹里，或者不能启动"


class PlayRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file_id: int | None = None
    target: str | None = None
    """游戏条目：要启动的程序，相对游戏文件夹的路径。"""
    choose: bool = False
    """游戏条目：不用记住的程序，重新给出候选。"""


class PlayCandidate(BaseModel):
    file_id: int
    path: str
    name: str
    size: int | None
    exists: bool
    target: str | None = None
    """游戏条目的候选程序相对游戏文件夹的路径，选定后放进请求的 target。"""


class PlayChoices(BaseModel):
    """多个视频或多个程序时不猜，把候选交还调用方，由它带 file_id 或 target 再请求一次。"""

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


def game_folders(session: Session, item: Item) -> list[MediaFile]:
    return list(
        session.scalars(
            select(MediaFile)
            .where(MediaFile.item_id == item.id, MediaFile.file_kind == "game")
            .order_by(MediaFile.path, MediaFile.id)
        )
    )


def _candidate(row: MediaFile) -> PlayCandidate:
    path = Path(row.path)
    return PlayCandidate(
        file_id=row.id, path=row.path, name=path.name, size=row.size, exists=path.is_file()
    )


def _choices(detail: str, candidates: list[PlayCandidate]) -> JSONResponse:
    content = PlayChoices(detail=detail, candidates=candidates).model_dump(mode="json")
    return JSONResponse(status_code=409, content=content)


def _pick(rows: list[MediaFile], file_id: int | None) -> MediaFile | None:
    """只有一个时用它；带了 file_id 时找对应的那个；多个又没带时返回 None 让调用方给候选。"""
    if file_id is None:
        return rows[0] if len(rows) == 1 else None
    found = next((row for row in rows if row.id == file_id), None)
    if found is None:
        raise UserError("这个条目没有这个文件", status_code=404)
    return found


@router.post(
    "/items/{item_id}/play",
    response_model=PlayResult,
    responses={409: {"model": PlayChoices}},
)
def play_item(
    item_id: int, session: SessionDep, services: ServicesDep, body: PlayRequest | None = None
) -> PlayResult | JSONResponse:
    item = get_item(session, item_id)
    request = body if body is not None else PlayRequest()
    games = game_folders(session, item)
    if games:
        return _launch_game(services, games, request)
    videos = playable_videos(session, item)
    if not videos:
        raise UserError("这个条目没有视频文件", status_code=404)
    file_id = request.file_id
    if file_id is None:
        if len(videos) > 1:
            return _choices(
                f"有 {len(videos)} 个视频，选一个再播放", [_candidate(row) for row in videos]
            )
        target = videos[0]
    else:
        found = next((row for row in videos if row.id == file_id), None)
        if found is None:
            raise UserError("这个条目没有这个视频文件", status_code=404)
        target = found
    if not Path(target.path).is_file():
        raise UserError(f"文件不在了：{target.path}", status_code=404)
    plugin, host = choose_player(services.plugins, services.launcher, services.config_store)
    _play(plugin, host, target.path)
    return PlayResult(
        player=plugin.id, player_name=plugin.name, file_id=target.id, path=target.path
    )


def _play(plugin: PlayerPlugin, host: PluginPlayerHost, path: str) -> None:
    try:
        plugin.play(host, path)
    except PlayerError as error:
        raise UserError(f"{plugin.name} 没能打开：{error.message}", status_code=502) from None
    except Exception as error:
        raise UserError(
            f"{plugin.name} 出错：{type(error).__name__}: {error}", status_code=502
        ) from None


def _inside(folder: Path, relative: object) -> Path | None:
    """relative 解析后仍在 folder 里、并且是个文件时，返回规范化后相对 folder 的路径。

    `sub\\a.exe`、`sub/a.exe`、`sub/../sub/a.exe` 得到同一个结果。
    """
    if not isinstance(relative, str) or not relative.strip():
        return None
    root = folder.resolve()
    try:
        path = (root / relative).resolve()
    except OSError, ValueError:
        return None
    if not path.is_relative_to(root) or path == root or not path.is_file():
        return None
    return path.relative_to(root)


def _targets(plugin: PlayerPlugin, host: PluginPlayerHost, folder: Path) -> list[Path]:
    try:
        found = plugin.targets(host, str(folder))
    except PlayerError as error:
        raise UserError(f"{plugin.name} 没能列出程序：{error.message}", status_code=502) from None
    except Exception as error:
        raise UserError(
            f"{plugin.name} 出错：{type(error).__name__}: {error}", status_code=502
        ) from None
    if not isinstance(found, list):
        return []
    targets: list[Path] = []
    for target in found:
        relative = _inside(folder, target)
        if relative is not None and relative not in targets:
            targets.append(relative)
    return targets


def _allowed(
    plugin: PlayerPlugin, host: PluginPlayerHost, folder: Path, target: str | None
) -> Path | None:
    """手选的和记住的程序都要过这一关：在文件夹里、存在，并且是 .exe 或插件给的候选之一。"""
    relative = _inside(folder, target)
    if relative is None:
        return None
    if relative.suffix.lower() == ".exe" or relative in _targets(plugin, host, folder):
        return relative
    return None


def _launch_game(
    services: Services, games: list[MediaFile], request: PlayRequest
) -> PlayResult | JSONResponse:
    row = _pick(games, request.file_id)
    if row is None:
        return _choices(
            f"有 {len(games)} 个游戏文件夹，选一个再启动",
            [
                PlayCandidate(
                    file_id=game.id,
                    path=game.path,
                    name=Path(game.path).name,
                    size=game.size,
                    exists=Path(game.path).is_dir(),
                )
                for game in games
            ],
        )
    folder = Path(row.path)
    if not folder.is_dir():
        raise UserError(f"游戏文件夹不在了：{row.path}", status_code=404)
    store = services.config_store
    plugin, host = choose_game_player(services.plugins, services.launcher, store)

    remembered = remembered_target(host, row.path)
    if request.target is not None:
        chosen = _allowed(plugin, host, folder, request.target)
        if chosen is None:
            raise UserError(BAD_TARGET, status_code=400)
    elif not request.choose and remembered is not None:
        chosen = _allowed(plugin, host, folder, remembered)
    else:
        chosen = None
    if chosen is None:
        targets = _targets(plugin, host, folder)
        if not targets:
            raise UserError("文件夹里没有可以启动的程序", status_code=404)
        if len(targets) > 1:
            return _choices(
                f"有 {len(targets)} 个可以启动的程序，选一个再启动",
                [_program(row, folder, relative) for relative in targets],
            )
        chosen = targets[0]
    launched = str(folder / chosen)
    _play(plugin, host, launched)
    if str(chosen) != remembered:
        try:
            remember_target(store, plugin, row.path, str(chosen))
        except Exception:
            logger.warning("记不住 %s 的启动程序 %s", row.path, chosen, exc_info=True)
    return PlayResult(player=plugin.id, player_name=plugin.name, file_id=row.id, path=launched)


def _program(row: MediaFile, folder: Path, relative: Path) -> PlayCandidate:
    path = folder / relative
    try:
        size: int | None = path.stat().st_size
    except OSError:
        size = None
    return PlayCandidate(
        file_id=row.id,
        path=str(path),
        name=path.name,
        size=size,
        exists=True,
        target=str(relative),
    )
