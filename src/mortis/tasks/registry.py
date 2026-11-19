from mortis.hooks.runtime import HOOK_TASK, handle_hook
from mortis.organize.service import handle_organize
from mortis.output.service import handle_arrange, handle_arrange_library
from mortis.scrape.service import handle_refresh_running_shows, handle_scrape, handle_scrape_library
from mortis.sources import imdb
from mortis.tasks.base import Handler
from mortis.tasks.schedule import REFRESH_RUNNING


def builtin_handlers() -> dict[str, Handler]:
    return {
        "organize": handle_organize,
        "scrape": handle_scrape,
        "scrape_library": handle_scrape_library,
        "arrange": handle_arrange,
        "arrange_library": handle_arrange_library,
        imdb.REFRESH_TASK: imdb.handle_refresh,
        REFRESH_RUNNING: handle_refresh_running_shows,
        HOOK_TASK: handle_hook,
    }
