"""Shared helpers for the dataset scripts: the manifest format and near-duplicate hashing."""

import csv
from pathlib import Path

import numpy as np
from PIL import Image

from app.config import ROOT_DIR

DATASETS_DIR = ROOT_DIR / "datasets"
FIELDS = ["file", "source", "license", "label", "split"]
# Image-level labels: damaged/intact judge the boxes, blocked/clear judge the walkway.
LABELS = ("damaged", "intact", "blocked", "clear")
SPLITS = ("dev", "eval")
HASH_SIDE = 8
DEFAULT_MAX_DISTANCE = 4


def dhash(img: Image.Image) -> int:
    """64-bit difference hash: brightness gradients of an 9x8 thumbnail, stable under rescaling and re-encoding."""
    small = np.asarray(img.convert("L").resize((HASH_SIDE + 1, HASH_SIDE), Image.Resampling.LANCZOS), dtype=np.int16)
    bits = (small[:, 1:] > small[:, :-1]).ravel()
    return int("".join("1" if b else "0" for b in bits), 2)


def distance(a: int, b: int) -> int:
    return (a ^ b).bit_count()


def split_for(sha: str) -> str:
    # Derived from the content hash, so a photo keeps its split on every run.
    return "eval" if int(sha[:8], 16) % 5 == 0 else "dev"


def read_manifest(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_manifest(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows({k: row.get(k, "") for k in FIELDS} for row in rows)
