import logging
from typing import Annotated

import openai
from fastapi import FastAPI, File, HTTPException, Path, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.pipeline.scan import ALLOWED_MIME_TYPES, InvalidImageError, load_cached, run_scan
from app.schemas import ScanResult

logger = logging.getLogger(__name__)

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
SCAN_ID_PATTERN = r"^[0-9a-f]{16}$"

settings = get_settings()

app = FastAPI(title="AIsleEye", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "models": {"vision_agent": settings.muse_model, "segmentation": settings.sam_model},
    }


@app.post("/scan", response_model=ScanResult)
async def scan(image: Annotated[UploadFile, File()]) -> ScanResult:
    if image.content_type not in ALLOWED_MIME_TYPES:
        raise HTTPException(415, "Only JPEG, PNG or WebP images are supported.")
    data = await image.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"Image exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit.")
    try:
        return await run_scan(data, image.content_type)
    except InvalidImageError as exc:
        raise HTTPException(422, str(exc)) from None
    except openai.APIError as exc:
        logger.warning("Model API call failed: %s", type(exc).__name__)
        raise HTTPException(502, "The vision model service is unavailable. Please try again shortly.") from None


@app.get("/scan/{scan_id}", response_model=ScanResult)
def get_scan(scan_id: Annotated[str, Path(pattern=SCAN_ID_PATTERN)]) -> ScanResult:
    result = load_cached(scan_id)
    if result is None:
        raise HTTPException(404, "Scan not found.")
    return result
