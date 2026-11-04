import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

import openai
from fastapi import FastAPI, File, HTTPException, Path, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from app import observability, replay, retention
from app.config import get_settings
from app.pipeline import scan as scan_pipeline
from app.pipeline.scan import ALLOWED_MIME_TYPES, InvalidImageError, load_cached, load_image, run_scan, scan_id_for
from app.replay import load_pinned
from app.routes_agent import router as agent_router
from app.routes_demo import router as demo_router
from app.routes_feedback import router as feedback_router
from app.routes_scores import router as scores_router
from app.schemas import ScanResult

logger = logging.getLogger(__name__)

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
SCAN_ID_PATTERN = r"^[0-9a-f]{16}$"

settings = get_settings()


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    retention.purge_expired(scan_pipeline.CACHE_DIR, get_settings().cache_ttl_days)
    observability.init()
    yield
    observability.shutdown()


app = FastAPI(title="AIsleEye", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(agent_router)
app.include_router(demo_router)
app.include_router(feedback_router)
app.include_router(scores_router)


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "models": {"vision_agent": settings.muse_model, "segmentation": settings.sam_model},
        "demo_mode": get_settings().demo_mode,
    }


def _replayed(result: ScanResult) -> ScanResult:
    return result.model_copy(update={"replay": True})


@app.post("/scan", response_model=ScanResult)
async def scan(image: Annotated[UploadFile, File()]) -> ScanResult:
    if image.content_type not in ALLOWED_MIME_TYPES:
        raise HTTPException(415, "Only JPEG, PNG or WebP images are supported.")
    data = await image.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"Image exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit.")
    try:
        if get_settings().demo_mode == "replay":
            load_image(data, image.content_type)  # still reject files that are not images
            saved = load_pinned(scan_id_for(data)) or load_cached(scan_id_for(data))
            if saved is None:
                raise HTTPException(404, "No saved replay result for this photo. Choose one of the demo photos.")
            return _replayed(saved)
        return await run_scan(data, image.content_type)
    except InvalidImageError as exc:
        raise HTTPException(422, str(exc)) from None
    except openai.APIError as exc:
        logger.warning("Model API call failed: %s", type(exc).__name__)
        # A failing or slow API must not sink the demo: fall back to the saved result, flagged as a replay.
        pinned = load_pinned(scan_id_for(data))
        if pinned is not None:
            return _replayed(pinned)
        raise HTTPException(502, "The vision model service is unavailable. Please try again shortly.") from None


@app.get("/scan/{scan_id}", response_model=ScanResult)
def get_scan(scan_id: Annotated[str, Path(pattern=SCAN_ID_PATTERN)]) -> ScanResult:
    result = replay.find_scan(scan_id)
    if result is None:
        raise HTTPException(404, "Scan not found.")
    return result


@app.delete("/scan/{scan_id}", status_code=204)
def delete_scan(scan_id: Annotated[str, Path(pattern=SCAN_ID_PATTERN)]) -> Response:
    # Only the cache: pinned demo results cannot be deleted through the API.
    if not retention.delete_scan(scan_id):
        raise HTTPException(404, "Scan not found.")
    return Response(status_code=204)
