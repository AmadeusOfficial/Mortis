from datetime import UTC, datetime

from fastapi import APIRouter
from pydantic import BaseModel

from mortis.errors import UserError
from mortis.tasks import schedule

router = APIRouter(prefix="/api/schedules")


class CronPreview(BaseModel):
    cron: str
    next_runs: list[str]


@router.get("/preview", response_model=CronPreview)
def preview_cron(cron: str) -> CronPreview:
    """接下来几次到点的时间，按本机时区；规则不对时 422 带原因。"""
    try:
        cleaned = schedule.check_cron(cron)
        runs = schedule.upcoming(cleaned, datetime.now(UTC))
    except schedule.CronError as error:
        raise UserError(str(error), status_code=422) from None
    return CronPreview(cron=cleaned, next_runs=[run.isoformat(timespec="minutes") for run in runs])
