from mortis.organize.service import handle_organize
from mortis.tasks.base import Handler


def builtin_handlers() -> dict[str, Handler]:
    return {"organize": handle_organize}
