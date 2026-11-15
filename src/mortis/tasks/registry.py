from mortis.organize.service import handle_organize
from mortis.output.service import (
    handle_rename,
    handle_rename_library,
    handle_write_files,
    handle_write_files_library,
)
from mortis.scrape.service import handle_scrape, handle_scrape_library
from mortis.tasks.base import Handler


def builtin_handlers() -> dict[str, Handler]:
    return {
        "organize": handle_organize,
        "scrape": handle_scrape,
        "scrape_library": handle_scrape_library,
        "write_files": handle_write_files,
        "rename": handle_rename,
        "write_files_library": handle_write_files_library,
        "rename_library": handle_rename_library,
    }
