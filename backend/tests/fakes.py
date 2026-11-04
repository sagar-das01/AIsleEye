from types import SimpleNamespace

import numpy as np
from meta_sam_parser._mask_codec import _encode_raster  # private encoder: only way to build real payloads offline

PATCHED_MODULES = ("app.pipeline.segment", "app.pipeline.inspect")


class FakeResponses:
    def __init__(self, handler):
        self.handler = handler
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        out = self.handler(kwargs)
        if isinstance(out, BaseException):
            raise out
        return SimpleNamespace(output_text=out)


def install_fake_model(monkeypatch, handler) -> FakeResponses:
    responses = FakeResponses(handler)
    fake = SimpleNamespace(responses=responses)
    for mod in PATCHED_MODULES:
        monkeypatch.setattr(f"{mod}.get_async_client", lambda: fake)
    return responses


def encode_mask(raster: np.ndarray) -> str:
    h, w = raster.shape
    return _encode_raster(raster.astype(np.uint8).ravel().tolist(), w, h)


def sam_record(obj_id: int, x1: int, y1: int, x2: int, y2: int, *, w: int, h: int, raster=None, c: float = 0.9) -> str:
    """x2/y2 inclusive, as SAM emits them; raster defaults to a full box."""
    if raster is None:
        raster = np.ones((y2 - y1 + 1, x2 - x1 + 1), dtype=np.uint8)
    rh, rw = raster.shape
    return (
        f"{obj_id}<|box;x1={x1};y1={y1};x2={x2};y2={y2};w={w};h={h};c={c}|>"
        f"<|mask;x=0;y=0;c={c};data={rh},{rw},{encode_mask(raster)}|>"
    )


def sam_line(*records: str) -> str:
    return "<0f>" + ",".join(records)


def concept_of(kwargs: dict) -> str:
    return kwargs["input"][0]["content"][0]["text"]
