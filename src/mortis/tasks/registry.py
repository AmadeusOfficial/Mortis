from mortis.organize.service import handle_organize
from mortis.output.service import handle_arrange, handle_arrange_library
from mortis.scrape.service import handle_scrape, handle_scrape_library
from mortis.sources import imdb
from mortis.tasks.base import Handler


def builtin_handlers() -> dict[str, Handler]:
    return {
        "organize": handle_organize,
        "scrape": handle_scrape,
        "scrape_library": handle_scrape_library,
        "arrange": handle_arrange,
        "arrange_library": handle_arrange_library,
        imdb.REFRESH_TASK: imdb.handle_refresh,
    }
