from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from mortis.api.deps import RunnerDep, SessionDep
from mortis.api.routes.items import get_item
from mortis.db.models import Item
from mortis.items import user_tags
from mortis.output.service import rewrite_if_arranged
from mortis.tasks.runner import TaskRunner

router = APIRouter(prefix="/api")


class UserTagOut(BaseModel):
    id: int
    name: str
    count: int
    """用到这个标签的作品数。"""


class ItemTagsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    names: list[str]


class ItemTagsOut(BaseModel):
    names: list[str]


class RenameRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str


class Impact(BaseModel):
    count: int
    """会影响的作品数。执行时原样交回，不一致就 409。"""


class MergeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_ids: list[int]
    target_id: int
    expected_count: int


class DeleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_count: int


def _rewrite(session: Session, runner: TaskRunner, item_ids: list[int]) -> None:
    for item_id in item_ids:
        item = session.get(Item, item_id)
        if item is not None:
            rewrite_if_arranged(session, runner, item)


def _out(session: Session, tag_id: int) -> UserTagOut:
    tag = user_tags.get_tag(session, tag_id)
    count = len(user_tags.tagged_items(session, [tag.id]))
    return UserTagOut(id=tag.id, name=tag.name, count=count)


@router.get("/data-types/{data_type_id}/user-tags", response_model=list[UserTagOut])
def list_user_tags(data_type_id: str, session: SessionDep) -> list[UserTagOut]:
    return [UserTagOut(**row) for row in user_tags.list_tags(session, data_type_id)]


@router.put("/items/{item_id}/user-tags", response_model=ItemTagsOut)
def put_item_tags(
    item_id: int, body: ItemTagsUpdate, session: SessionDep, runner: RunnerDep
) -> ItemTagsOut:
    item = get_item(session, item_id)
    names = user_tags.set_item_tags(session, item, body.names)
    rewrite_if_arranged(session, runner, item)
    return ItemTagsOut(names=names)


@router.get("/user-tags/merge/impact", response_model=Impact)
def merge_impact(
    session: SessionDep, target_id: int, source_ids: Annotated[list[int], Query()]
) -> Impact:
    target = user_tags.get_tag(session, target_id)
    sources = user_tags.merge_sources(session, source_ids, target)
    return Impact(count=len(user_tags.tagged_items(session, [tag.id for tag in sources])))


@router.post("/user-tags/merge", response_model=UserTagOut)
def merge_user_tags(body: MergeRequest, session: SessionDep, runner: RunnerDep) -> UserTagOut:
    target = user_tags.get_tag(session, body.target_id)
    sources = user_tags.merge_sources(session, body.source_ids, target)
    affected = user_tags.merge_tags(session, sources, target, body.expected_count)
    _rewrite(session, runner, affected)
    return _out(session, target.id)


@router.patch("/user-tags/{tag_id}", response_model=UserTagOut)
def rename_user_tag(
    tag_id: int, body: RenameRequest, session: SessionDep, runner: RunnerDep
) -> UserTagOut:
    tag = user_tags.get_tag(session, tag_id)
    _rewrite(session, runner, user_tags.rename_tag(session, tag, body.name))
    return _out(session, tag_id)


@router.get("/user-tags/{tag_id}/impact", response_model=Impact)
def delete_impact(tag_id: int, session: SessionDep) -> Impact:
    tag = user_tags.get_tag(session, tag_id)
    return Impact(count=len(user_tags.tagged_items(session, [tag.id])))


@router.delete("/user-tags/{tag_id}", status_code=204)
def delete_user_tag(
    tag_id: int, body: DeleteRequest, session: SessionDep, runner: RunnerDep
) -> None:
    tag = user_tags.get_tag(session, tag_id)
    _rewrite(session, runner, user_tags.delete_tag(session, tag, body.expected_count))
