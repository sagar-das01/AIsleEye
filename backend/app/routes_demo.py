from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path
from fastapi.responses import FileResponse

from app import replay
from app.auth import Permission, require_permission

router = APIRouter(dependencies=[Depends(require_permission(Permission.SCAN))])


@router.get("/demo")
def demo_list() -> list[dict]:
    return replay.demo_entries()


@router.get("/demo/{name}.jpg")
def demo_image(name: Annotated[str, Path(pattern=r"^[a-z0-9_-]{1,40}$")]) -> FileResponse:
    path = replay.demo_image_path(name)
    if path is None:
        raise HTTPException(404, "Demo photo not found.")
    return FileResponse(path, media_type="image/jpeg")
