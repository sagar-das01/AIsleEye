import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

import openai
from fastapi import Depends, FastAPI, File, HTTPException, Path, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import annotations, observability, replay, retention, workflow
from app.config import get_settings
from app.auth import Permission, require_permission
from app.pipeline import scan as scan_pipeline
from app.pipeline.scan import ALLOWED_MIME_TYPES, InvalidImageError, load_cached, load_image, run_scan, scan_id_for
from app.replay import load_pinned
from app.routes_agent import router as agent_router
from app.routes_auth import router as auth_router
from app.routes_demo import router as demo_router
from app.routes_feedback import router as feedback_router
from app.routes_scores import router as scores_router
from app.routes_tickets import router as tickets_router
from app.schemas import ScanResult

logger = logging.getLogger(__name__)

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
SCAN_ID_PATTERN = r"^[0-9a-f]{16}$"

settings = get_settings()


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    retention.purge_expired(scan_pipeline.CACHE_DIR, get_settings().cache_ttl_days)
    if get_settings().demo_sign_in_enabled:
        logger.warning("DEMO_SIGN_IN_ENABLED is on: anyone who can reach this server can pick any role. Local demos only.")
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


@app.middleware("http")
async def private_responses(request: Request, call_next):
    response = await call_next(request)
    # Per-user data must never land in a shared or browser cache that outlives a role switch.
    response.headers.setdefault("Cache-Control", "no-store")
    return response


app.include_router(auth_router)
app.include_router(agent_router)
app.include_router(tickets_router)
app.include_router(demo_router)
app.include_router(feedback_router)
app.include_router(scores_router)


@app.exception_handler(workflow.WorkflowError)
async def workflow_error(_request: Request, exc: workflow.WorkflowError) -> JSONResponse:
    if isinstance(exc, workflow.StoreUnavailable):
        logger.error("The ticket workflow store is unavailable or invalid")
    return JSONResponse({"detail": exc.message}, status_code=exc.status_code)


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "models": {"vision_agent": settings.muse_model, "segmentation": settings.sam_model},
        "demo_mode": get_settings().demo_mode,
        "demo_sign_in": get_settings().demo_sign_in_enabled,
    }


def _replayed(result: ScanResult) -> ScanResult:
    return annotations.apply(result.model_copy(update={"replay": True}))


@app.post("/scan", response_model=ScanResult, dependencies=[Depends(require_permission(Permission.SCAN))])
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
        # A re-upload of a reviewed photo shows the supervisor's corrections, not the raw model output again.
        return annotations.apply(await asyncio.wait_for(run_scan(data, image.content_type), get_settings().scan_timeout_s))
    except InvalidImageError as exc:
        raise HTTPException(422, str(exc)) from None
    except (openai.APITimeoutError, TimeoutError) as exc:
        logger.warning("Scan timed out: %s", type(exc).__name__)
        return _fallback_or_raise(data, 504, "The vision model service took too long. Please try again, or use a smaller photo.")
    except openai.APIError as exc:
        logger.warning("Model API call failed: %s", type(exc).__name__)
        return _fallback_or_raise(data, 502, "The vision model service is unavailable. Please try again shortly.")


def _fallback_or_raise(data: bytes, status: int, message: str) -> ScanResult:
    # A failing or slow API must not sink the demo: fall back to the saved result, flagged as a replay.
    pinned = load_pinned(scan_id_for(data))
    if pinned is not None:
        return _replayed(pinned)
    raise HTTPException(status, message) from None


@app.get("/scan/{scan_id}", response_model=ScanResult, dependencies=[Depends(require_permission(Permission.REVIEW))])
def get_scan(scan_id: Annotated[str, Path(pattern=SCAN_ID_PATTERN)]) -> ScanResult:
    result = replay.find_scan(scan_id)
    if result is None:
        raise HTTPException(404, "Scan not found.")
    return annotations.apply(result)


@app.delete("/scan/{scan_id}", status_code=204, dependencies=[Depends(require_permission(Permission.DELETE_SCAN))])
def delete_scan(scan_id: Annotated[str, Path(pattern=SCAN_ID_PATTERN)]) -> Response:
    # Only the cache: pinned demo results cannot be deleted through the API.
    if not retention.delete_scan(scan_id):
        raise HTTPException(404, "Scan not found.")
    annotations.delete(scan_id)
    return Response(status_code=204)
