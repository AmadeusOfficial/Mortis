"""智能体能调用的工具。每个工具只调用宿主现有的函数，不拼 SQL 更新；出错把中文信息交还模型。"""

import json
import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from mortis import libraries
from mortis.agent import recognize
from mortis.agent.check import scrub, secret_values
from mortis.agent.client import ToolCall
from mortis.agent.context import Result, ToolContext
from mortis.api.routes.items import detail_of, scrape_work
from mortis.api.routes.libraries import enqueue_organize
from mortis.api.routes.play import PlayChoices, PlayRequest, play
from mortis.db.models import DataType, Item, Library
from mortis.errors import UserError
from mortis.items.detail import image_url, read_value, year_of
from mortis.items.edit import ItemError, edit_fields
from mortis.output.service import rewrite_if_arranged
from mortis.scrape.apply import level_fields

logger = logging.getLogger("mortis.agent")

SEARCH_LIMIT = 20
TEXT_LIMIT = 300
PEOPLE_LIMIT = 10
EPISODE_LIMIT = 200
CANDIDATE_LIMIT = 20
TOP_KINDS = ("work", "collection")
MAIN_FILE_KINDS = ("video", "game")
RECOGNIZE_TOOLS = frozenset({"rematch_item", "bind_file"})


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    """JSON Schema。只用 integer、string、object 和 enum，参数检查按它来。"""
    run: Callable[[ToolContext, dict[str, Any]], Result]
    needs_approval: bool = False
    """每次调用都要用户先批准。"""
    describe: Callable[[ToolContext, dict[str, Any]], Result] | None = None
    """批准卡片上的一项：现在是什么、要换成什么。不给时列出参数。"""

    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


TOOLS: dict[str, Tool] = {}


def register(tool: Tool) -> Tool:
    TOOLS[tool.name] = tool
    return tool


def tool_schemas() -> list[dict[str, Any]]:
    return [tool.schema() for tool in TOOLS.values()]


def run_tool(ctx: ToolContext, name: str, arguments: str | dict[str, Any]) -> Result:
    """执行一次工具调用。不抛异常：未知工具、参数不对、执行出错都返回 {"error": ...}。

    错误信息会交给模型，里面出现的代理用户名、口令和各种 key 都遮掉。
    """
    tool = TOOLS.get(name)
    if tool is None:
        return {"error": f"没有这个工具：{name}"}
    parsed = _parse_arguments(arguments)
    if parsed is None:
        return {"error": "参数不是合法的 JSON"}
    problem = _check_arguments(tool.parameters, parsed)
    if problem is not None:
        return {"error": problem}
    try:
        return tool.run(ctx, parsed)
    except UserError as error:
        ctx.session.rollback()
        secrets = secret_values(ctx.services.config_store.load_secrets())
        return {"error": scrub(error.message, *secrets)}
    except Exception as error:
        ctx.session.rollback()
        logger.exception("工具 %s 出错", name)
        return {"error": f"执行 {name} 出错：{type(error).__name__}"}


def batch_update_targets(calls: Iterable[ToolCall]) -> list[int]:
    """一组调用里 update_fields 涉及的不同条目，两个及以上就算批量修改。参数不对的不算。"""
    targets: set[int] = set()
    for call in calls:
        if call.name != "update_fields":
            continue
        parsed = _parse_arguments(call.arguments)
        if parsed is None or _check_arguments(TOOLS[call.name].parameters, parsed) is not None:
            continue
        targets.add(parsed["item_id"])
    return sorted(targets)


def calls_needing_approval(calls: list[ToolCall], changed: Iterable[int] = ()) -> list[ToolCall]:
    """这一轮里要等用户批准的调用：标了 needs_approval 的；批量修改时这一轮的全部 update_fields。

    changed 是这次提问里前几轮已经改过的条目。批量按一次提问算：这一轮要改的加上已改过的
    达到两个及以上，就算批量，免得模型一轮改一个绕过批准。
    """
    targets = set(batch_update_targets(calls))
    batch = bool(targets) and len(targets | set(changed)) >= 2
    held: list[ToolCall] = []
    for call in calls:
        tool = TOOLS.get(call.name)
        if tool is None:
            continue
        if tool.needs_approval or (batch and call.name == "update_fields"):
            held.append(call)
    return held


def approval_summary(calls: list[ToolCall], changed: Iterable[int] = ()) -> str:
    if all(call.name == "update_fields" for call in calls):
        count = len(batch_update_targets(calls))
        done = len(set(changed) - set(batch_update_targets(calls)))
        if done:
            return f"这次已经改了 {done} 个条目，再改 {count} 个要你批准后才会执行"
        return f"要修改 {count} 个条目，批准后才会执行"
    if all(call.name in RECOGNIZE_TOOLS for call in calls):
        return f"要重新识别 {len(calls)} 项，批准后才会执行"
    return f"有 {len(calls)} 个操作要你批准后才会执行"


def describe_call(ctx: ToolContext, call: ToolCall) -> Result:
    """批准卡片上的一项。参数不对时只列出原样的参数，执行时再报错。"""
    tool = TOOLS.get(call.name)
    parsed = _parse_arguments(call.arguments) or {}
    if tool is None or tool.describe is None or _check_arguments(tool.parameters, parsed):
        return {"tool": call.name, "arguments": parsed}
    try:
        return {"tool": call.name, **tool.describe(ctx, parsed)}
    except UserError as error:
        ctx.session.rollback()
        return {"tool": call.name, "arguments": parsed, "error": error.message}


def _describe_update(ctx: ToolContext, args: dict[str, Any]) -> Result:
    item = ctx.session.get(Item, args["item_id"])
    labels: dict[str, str] = {}
    if item is not None:
        labels = {
            f.field_key: f.label for f in level_fields(ctx.session, item.data_type_id, item.kind)
        }
    values: dict[str, Any] = args["values"]
    return {
        "item_id": args["item_id"],
        "title": item.title if item is not None else f"条目 {args['item_id']}",
        "values": values,
        "fields": [
            {"key": key, "label": labels.get(key, key), "value": value}
            for key, value in values.items()
        ],
    }


def _parse_arguments(arguments: str | dict[str, Any]) -> dict[str, Any] | None:
    if isinstance(arguments, dict):
        return dict(arguments)
    try:
        parsed = json.loads(arguments or "{}")
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _check_arguments(schema: dict[str, Any], arguments: dict[str, Any]) -> str | None:
    """按 schema 检查并就地规整参数：整数可以写成数字字符串，null 当作没给。"""
    properties: dict[str, Any] = schema.get("properties", {})
    unknown = [key for key in arguments if key not in properties]
    if unknown:
        return f"不认识的参数：{'、'.join(unknown)}"
    for key in [key for key, value in arguments.items() if value is None]:
        del arguments[key]
    for key in schema.get("required", []):
        if key not in arguments:
            return f"缺少参数 {key}"
    for key, value in arguments.items():
        spec = properties[key]
        expected = spec.get("type")
        if expected == "integer":
            if isinstance(value, str) and value.strip().isdigit():
                value = arguments[key] = int(value.strip())
            if isinstance(value, bool) or not isinstance(value, int):
                return f"参数 {key} 要是整数"
        elif expected == "string" and not isinstance(value, str):
            return f"参数 {key} 要是文字"
        elif expected == "object" and not isinstance(value, dict):
            return f"参数 {key} 要是对象"
        if "enum" in spec and value not in spec["enum"]:
            return f"参数 {key} 只能是 {'、'.join(spec['enum'])}"
    return None


def _item(session: Session, item_id: int) -> Item:
    item = session.get(Item, item_id)
    if item is None:
        raise ItemError(f"没有这个条目：{item_id}", status_code=404)
    return item


def _structure_label(kind: str, structure: str | None) -> str:
    if kind == "collection":
        return "系列"
    if kind == "season":
        return "季"
    if kind == "episode":
        return "集"
    return "剧" if structure == "series" else "单部"


def _structure_of(session: Session, item: Item) -> str:
    data_type = session.get(DataType, item.data_type_id)
    return _structure_label(item.kind, data_type.structure if data_type else None)


def _clip(text: str) -> str:
    return text if len(text) <= TEXT_LIMIT else text[:TEXT_LIMIT] + "…"


def _search_items(ctx: ToolContext, args: dict[str, Any]) -> Result:
    term = args["query"].strip()
    if not term:
        raise ItemError("query 不能为空")
    kinds = (args["kind"],) if "kind" in args else TOP_KINDS
    conditions = [
        Item.kind.in_(kinds),
        Item.title.icontains(term, autoescape=True)
        | Item.original_title.icontains(term, autoescape=True)
        | Item.sort_title.icontains(term, autoescape=True),
    ]
    if "library_id" in args:
        conditions.append(Item.library_id == args["library_id"])
    session = ctx.session
    total = session.scalar(select(func.count()).select_from(Item).where(*conditions)) or 0
    exact = case((func.lower(Item.title) == term.lower(), 0), else_=1)
    rows = session.execute(
        select(Item, Library.name, DataType.structure)
        .join(Library, Library.id == Item.library_id)
        .join(DataType, DataType.id == Item.data_type_id)
        .where(*conditions)
        .order_by(exact, func.coalesce(Item.sort_title, Item.title), Item.id)
        .limit(SEARCH_LIMIT)
    ).all()
    collection_ids = [item.id for item, _, _ in rows if item.kind == "collection"]
    first_dates = dict(
        session.execute(
            select(Item.parent_id, func.min(Item.release_date))
            .where(Item.parent_id.in_(collection_ids), Item.kind == "work")
            .group_by(Item.parent_id)
        ).all()
    )
    items = [
        {
            "id": item.id,
            "title": item.title,
            "original_title": item.original_title,
            "year": year_of(item.release_date or first_dates.get(item.id)),
            "library_id": item.library_id,
            "library_name": library_name,
            "data_type": item.data_type_id,
            "kind": item.kind,
            "structure": _structure_label(item.kind, structure),
            "poster_url": image_url(item.poster_path),
        }
        for item, library_name, structure in rows
    ]
    result: Result = {"items": items, "total": total}
    if total > len(items):
        result["note"] = f"共 {total} 条，只列出前 {SEARCH_LIMIT} 条，换个更具体的词再查"
    return result


def _compact_value(value_type: str, stored_in: str, value: Any) -> Any:
    if stored_in == "people":
        names = [person["name"] for person in value]
        if len(names) > PEOPLE_LIMIT:
            return names[:PEOPLE_LIMIT] + [f"等 {len(names)} 人"]
        return names
    if stored_in == "ratings":
        return [
            {"source": r["source"], "rating": r["rating"], "max_rating": r["max_rating"]}
            for r in value
        ]
    if isinstance(value, str):
        return _clip(value)
    return value


def _get_item(ctx: ToolContext, args: dict[str, Any]) -> Result:
    session = ctx.session
    item = _item(session, args["item_id"])
    detail = detail_of(session, item)
    fields: dict[str, Any] = {}
    editable: list[str] = []
    for section in detail["sections"]:
        for field in section["fields"]:
            if field["editable"]:
                editable.append(field["key"])
            if field["value_type"] == "images" or field["value"] in (None, "", []):
                continue
            fields[field["key"]] = _compact_value(
                field["value_type"], field["stored_in"], field["value"]
            )
    direct_files = sum(1 for f in detail["files"] if f["file_kind"] in MAIN_FILE_KINDS)
    result: Result = {
        "id": item.id,
        "title": item.title,
        "year": detail["year"],
        "library_id": item.library_id,
        "library_name": detail["library_name"],
        "data_type": item.data_type_id,
        "kind": item.kind,
        "structure": _structure_of(session, item),
        "fields": fields,
        "editable": editable,
        "locked": json.loads(item.locked_fields),
        "external_ids": detail["external_ids"],
        "file_count": direct_files + sum(s["file_count"] for s in detail["seasons"]),
    }
    if detail["guess"] is not None:
        result["file"] = detail["guess"].model_dump()
    if detail["collection"] is not None:
        result["collection"] = {
            "id": detail["collection"]["id"],
            "title": detail["collection"]["title"],
        }
    if detail["show"] is not None:
        result["show"] = {"id": detail["show"]["id"], "title": detail["show"]["title"]}
        result["position"] = detail["position"]
    if item.kind == "collection":
        result["movies"] = [
            {"id": part["id"], "title": part["title"], "year": part["year"]}
            for part in detail["parts"]
        ]
    if detail["seasons"]:
        result["seasons"], omitted = _seasons(detail["seasons"])
        if omitted:
            result["note"] = f"集太多，省略了后面的 {omitted} 集"
    return result


def _seasons(seasons: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    room = EPISODE_LIMIT
    omitted = 0
    out: list[dict[str, Any]] = []
    for season in seasons:
        episodes = season["episodes"]
        kept = episodes[: max(room, 0)]
        omitted += len(episodes) - len(kept)
        room -= len(kept)
        out.append(
            {
                "id": season["id"],
                "number": season["number"],
                "title": season["title"],
                "episodes": [
                    {
                        "id": episode["id"],
                        "number": episode["number"],
                        "title": episode["title"],
                        "has_file": episode["has_file"],
                    }
                    for episode in kept
                ],
            }
        )
    return out, omitted


def _update_fields(ctx: ToolContext, args: dict[str, Any]) -> Result:
    session = ctx.session
    item = _item(session, args["item_id"])
    values: dict[str, Any] = args["values"]
    if not values:
        raise ItemError("values 里至少要有一个字段")
    edit_fields(session, item, values)
    rewrite_if_arranged(session, ctx.runner, item)
    fields = {f.field_key: f for f in level_fields(session, item.data_type_id, item.kind)}
    return {
        "item_id": item.id,
        "title": item.title,
        "values": {
            key: _compact_value(
                fields[key].value_type,
                fields[key].stored_in,
                read_value(session, item, fields[key]),
            )
            for key in values
        },
        "locked": json.loads(item.locked_fields),
    }


def _scrape_item(ctx: ToolContext, args: dict[str, Any]) -> Result:
    item = _item(ctx.session, args["item_id"])
    task_id = scrape_work(ctx.session, ctx.runner, item, force=True)
    return {"task_id": task_id, "item_id": item.id, "title": item.title}


def _scan_library(ctx: ToolContext, args: dict[str, Any]) -> Result:
    library = libraries.get_library(ctx.session, args["library_id"])
    task_id = enqueue_organize(ctx.session, ctx.runner, library, scrape=None)
    return {"task_id": task_id, "library_id": library.id, "library_name": library.name}


def _play_item(ctx: ToolContext, args: dict[str, Any]) -> Result:
    item = _item(ctx.session, args["item_id"])
    request = PlayRequest(file_id=args.get("file_id"), target=args.get("target"))
    result = play(ctx.session, ctx.services, item, request, listed_only=True)
    if isinstance(result, PlayChoices):
        candidates = [c.model_dump(mode="json") for c in result.candidates[:CANDIDATE_LIMIT]]
        return {"needs_choice": True, "detail": result.detail, "candidates": candidates}
    return {
        "played": True,
        "title": item.title,
        "player_name": result.player_name,
        "file_id": result.file_id,
        "path": result.path,
    }


ITEM_ID = {"type": "integer", "description": "条目 id，从 search_items 的结果里取"}

register(
    Tool(
        name="search_items",
        description=(
            "按标题、原名、排序名检索所有库里的作品和系列（不含季和集），最多返回 20 条。"
            "播放、修改之前先用它找到条目 id；同名的有多条时把候选列给用户选。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "标题里的关键词"},
                "library_id": {"type": "integer", "description": "只在这个库里找"},
                "kind": {
                    "type": "string",
                    "enum": list(TOP_KINDS),
                    "description": "work 是作品（电影、剧、游戏），collection 是系列电影",
                },
            },
            "required": ["query"],
        },
        run=_search_items,
    )
)
register(
    Tool(
        name="get_item",
        description=(
            "查看一个条目的详情：字段值、能修改的字段、锁定的字段、外部编号、文件数；"
            "剧带季和集的列表，系列带其中的电影。"
        ),
        parameters={
            "type": "object",
            "properties": {"item_id": ITEM_ID},
            "required": ["item_id"],
        },
        run=_get_item,
    )
)
register(
    Tool(
        name="update_fields",
        description=(
            "修改一个条目的字段，改过的字段会被锁定，以后刮削不覆盖。"
            "字段键用 get_item 结果里 editable 列出的；标签类字段给一组文字，日期写成 1999-03-30。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "item_id": ITEM_ID,
                "values": {
                    "type": "object",
                    "description": '要改的字段和新值，如 {"title": "教父"}',
                },
            },
            "required": ["item_id", "values"],
        },
        run=_update_fields,
        describe=_describe_update,
    )
)
register(
    Tool(
        name="scrape_item",
        description=(
            "重新刮削一部电影、剧或游戏，提交到任务队列，返回任务 id。锁定的字段不会被覆盖。"
        ),
        parameters={
            "type": "object",
            "properties": {"item_id": ITEM_ID},
            "required": ["item_id"],
        },
        run=_scrape_item,
    )
)
register(
    Tool(
        name="scan_library",
        description="扫描一个媒体库，整理新加的文件，提交到任务队列，返回任务 id。",
        parameters={
            "type": "object",
            "properties": {"library_id": {"type": "integer", "description": "库 id"}},
            "required": ["library_id"],
        },
        run=_scan_library,
    )
)
register(
    Tool(
        name="play_item",
        description=(
            "用本机播放器播放电影或一集，或者启动游戏。剧和季不能直接播放，要给集的 id。"
            "返回 needs_choice 时有多个视频或程序，把候选列给用户选，"
            "再带上选中的 file_id（游戏还要带 target）调用一次，不要自己选。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "item_id": ITEM_ID,
                "file_id": {"type": "integer", "description": "候选里的 file_id"},
                "target": {"type": "string", "description": "游戏候选里的 target"},
            },
            "required": ["item_id"],
        },
        run=_play_item,
    )
)
FILE_ID = {"type": "integer", "description": "未识别文件的 file_id，从 list_unmatched 的结果里取"}
SOURCE = {
    "type": "string",
    "description": "候选的 source，电影、剧、动漫是 tmdb，其他类型是来源插件 id",
}
EXTERNAL_ID = {"type": "string", "description": "候选的 external_id"}
register(
    Tool(
        name="search_candidates",
        description=(
            "给一个识别错或没识别的条目、文件搜外部候选，最多 10 个。item_id 和 file_id 二选一；"
            "query、year 不给时用从文件名解析出的标题和年份。结果原样列给用户选，不要自己选。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "item_id": ITEM_ID,
                "file_id": FILE_ID,
                "query": {"type": "string", "description": "搜索词，默认是文件名解析出的标题"},
                "year": {"type": "integer", "description": "年份，默认是文件名解析出的年份"},
            },
        },
        run=recognize.search_candidates,
    )
)
register(
    Tool(
        name="list_unmatched",
        description="列出没识别出作品的文件，带文件名解析出的标题和年份，最多 50 条。",
        parameters={
            "type": "object",
            "properties": {"library_id": {"type": "integer", "description": "只看这个库"}},
        },
        run=recognize.list_unmatched,
    )
)
register(
    Tool(
        name="rematch_item",
        description=(
            "把一部识别错的作品换成用户从 search_candidates 里选定的候选，然后重新刮削。"
            "锁定的字段保留。每次都要用户批准，只在用户选定之后调用。"
        ),
        parameters={
            "type": "object",
            "properties": {"item_id": ITEM_ID, "source": SOURCE, "external_id": EXTERNAL_ID},
            "required": ["item_id", "source", "external_id"],
        },
        run=recognize.rematch_item,
        needs_approval=True,
        describe=recognize.describe_rematch,
    )
)
register(
    Tool(
        name="bind_file",
        description=(
            "把一个未识别的文件绑到用户从 search_candidates 里选定的候选上，然后刮削。"
            "每次都要用户批准，只在用户选定之后调用。"
        ),
        parameters={
            "type": "object",
            "properties": {"file_id": FILE_ID, "source": SOURCE, "external_id": EXTERNAL_ID},
            "required": ["file_id", "source", "external_id"],
        },
        run=recognize.bind_file,
        needs_approval=True,
        describe=recognize.describe_bind,
    )
)
