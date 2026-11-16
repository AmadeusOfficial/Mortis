"""按剧、季、文件三个模板算出一部剧的目录和文件该叫什么。只算不动，有冲突整部剧跳过。"""

from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from mortis.config.models import TvOutputConfig
from mortis.db.models import Item, MediaFile
from mortis.organize.episodes import parse_episode
from mortis.organize.names import strip_stack, subtitle_belongs_to
from mortis.output.tv import show_layout
from mortis.rename.plan import Move, RenamePlan, _check_conflicts, _inside, tokens_of
from mortis.rename.template import render

_EDGE = " ._-"


def show_tokens(session: Session, show: Item) -> dict[str, str | None]:
    base = tokens_of(session, show)
    return {
        "showTitle": show.title,
        "showOriginalTitle": show.original_title,
        "showYear": base["year"],
        "tmdb": base["tmdb"],
        "imdb": base["imdb"],
    }


def _episode_numbers(video: Path, number: int | None) -> tuple[str, str]:
    """（episodeNr, episodeNr2）。一个文件里有多集时写成 1E2、01E02。"""
    parsed = parse_episode(video.stem, video.parent.name)
    episodes = list(parsed.episodes) if parsed and parsed.episodes[0] == number else [number or 0]
    return "E".join(str(n) for n in episodes), "E".join(f"{n:02d}" for n in episodes)


def _renamed(name: str, old: str, new: str, *, subtitle: bool) -> str:
    if subtitle:
        return new + name[len(old) :] if subtitle_belongs_to(name, old) else name
    return new + name[len(old) :] if name.startswith(old) else name


def plan_show_rename(session: Session, show: Item, output: TvOutputConfig) -> RenamePlan:
    plan = RenamePlan(show.id)
    layout = show_layout(session, show)
    if layout.folder is None:
        plan.problems.append("剧的视频直接放在库根目录，先放进剧自己的目录再整理")
        return plan
    folder = layout.folder
    base = show_tokens(session, show)

    def name(template: str, tokens: dict[str, str | None], space: str) -> str:
        return render(
            template, tokens, space=space, colon=output.colon_replacement, fullwidth_colon=False
        )

    try:
        show_name = name(output.show_template, base, output.show_space)
    except ValueError as error:
        plan.problems.append(str(error))
        return plan
    new_folder = folder.parent / show_name
    if folder.name != show_name:
        plan.folder_from, plan.folder_to = str(folder), str(new_folder)

    handled: set[int] = set()
    old_dirs: set[Path] = set()

    def move(row: MediaFile, target: Path) -> None:
        handled.add(row.id)
        plan.moves.append(Move(row.id, row.path, str(target)))

    for season in layout.seasons:
        if not season.episodes:
            continue
        number = season.item.index_number or 0
        tokens = base | {"seasonNr": str(number), "seasonNr2": f"{number:02d}"}
        try:
            season_dir = new_folder / name(output.season_template, tokens, output.season_space)
        except ValueError as error:
            plan.problems.append(str(error))
            return plan
        for row in session.scalars(select(MediaFile).where(MediaFile.item_id == season.item.id)):
            move(row, season_dir / Path(row.path).name)
        for episode in dict.fromkeys(e.item for e in season.episodes):
            rows = list(
                session.scalars(
                    select(MediaFile)
                    .where(MediaFile.item_id == episode.id)
                    .order_by(MediaFile.path)
                )
            )
            videos = [r for r in rows if r.file_kind == "video"]
            first = Path(videos[0].path)
            nr, nr2 = _episode_numbers(first, episode.index_number)
            try:
                stem = name(
                    output.file_template,
                    tokens | {"episodeNr": nr, "episodeNr2": nr2, "title": episode.title},
                    output.file_space,
                )
            except ValueError as error:
                plan.problems.append(str(error))
                return plan
            renames: list[tuple[str, str]] = []
            for video in videos:
                path = Path(video.path)
                old, part = strip_stack(path.stem)
                old = old.rstrip(_EDGE) if part else path.stem
                renames.append((old, stem))
                old_dirs.add(path.parent)
                move(video, season_dir / f"{stem}{path.stem[len(old) :]}{path.suffix}")
            for row in rows:
                if row.file_kind == "video":
                    continue
                file_name = Path(row.path).name
                for old, new in renames:
                    renamed = _renamed(file_name, old, new, subtitle=row.file_kind == "subtitle")
                    if renamed != file_name:
                        file_name = renamed
                        break
                move(row, season_dir / file_name)

    for row in session.scalars(select(MediaFile).where(MediaFile.library_id == show.library_id)):
        if row.id not in handled and _inside(Path(row.path), folder):
            move(row, new_folder / Path(row.path).relative_to(folder))
    for old in sorted(old_dirs, key=lambda p: -len(p.parts)):
        if old != folder and _inside(old, folder):
            plan.empty_dirs.append(str(new_folder / old.relative_to(folder)))
    _check_conflicts(plan)
    return plan
