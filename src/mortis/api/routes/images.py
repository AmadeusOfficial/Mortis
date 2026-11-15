from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from mortis.api.deps import ServicesDep
from mortis.scrape.images import ImageStore

router = APIRouter(prefix="/api/images")


@router.get("/{rel:path}")
def read_image(rel: str, services: ServicesDep) -> FileResponse:
    path = ImageStore(services.data_dir).resolve(rel)
    if path is None:
        raise HTTPException(status_code=404, detail="没有这张图")
    return FileResponse(path, headers={"Cache-Control": "public, max-age=31536000, immutable"})
