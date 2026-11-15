from mortis.organize.service import handle_organize
from mortis.scrape.service import handle_scrape, handle_scrape_library
from mortis.tasks.base import Handler


def builtin_handlers() -> dict[str, Handler]:
    return {
        "organize": handle_organize,
        "scrape": handle_scrape,
        "scrape_library": handle_scrape_library,
    }
