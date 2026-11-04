"""Sample photos from a folder into the gitignored datasets/ directory.

Usage (from backend/):
  python -m scripts.prepare_dataset SRC --source NAME --license LICENSE [--label damaged] [--n 50] [--seed 0]

Every photo goes through the same preprocessing as an upload (validated, EXIF-oriented, metadata stripped, <= 2048 px,
re-encoded as JPEG), is renamed after the hash of what was written, and is dropped if it is a near-duplicate of one
already kept. An empty --label leaves the photo for a person to label.
"""

import argparse
import hashlib
import io
import random
import sys
from pathlib import Path

from PIL import Image

from app.pipeline.preprocess import InvalidImageError, encode_jpeg, load_image
from scripts.dataset_utils import (
    DATASETS_DIR,
    DEFAULT_MAX_DISTANCE,
    LABELS,
    dhash,
    distance,
    read_manifest,
    split_for,
    write_manifest,
)

MIME_BY_SUFFIX = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}


def prepare(
    src: Path, out: Path, *, source: str, license: str, label: str = "", n: int = 50, seed: int = 0,
    max_distance: int = DEFAULT_MAX_DISTANCE,
) -> dict[str, int]:
    if label and label not in LABELS:
        raise ValueError(f"label must be one of {list(LABELS)} or empty")
    if not license.strip():
        raise ValueError("license is required: record where the photos may be used")
    candidates = sorted(p for p in Path(src).rglob("*") if p.is_file() and p.suffix.lower() in MIME_BY_SUFFIX)
    random.Random(seed).shuffle(candidates)

    processed = out / "processed"
    processed.mkdir(parents=True, exist_ok=True)
    rows = read_manifest(out / "manifest.csv")
    seen = [dhash(Image.open(processed / r["file"])) for r in rows if (processed / r["file"]).is_file()]
    counts = {"added": 0, "invalid": 0, "duplicate": 0}

    for path in candidates:
        if counts["added"] >= n:
            break
        try:
            data = encode_jpeg(load_image(path.read_bytes(), MIME_BY_SUFFIX[path.suffix.lower()]))
        except (InvalidImageError, OSError):
            counts["invalid"] += 1
            continue
        sha = hashlib.sha256(data).hexdigest()[:16]
        target = processed / f"{sha}.jpg"
        h = dhash(Image.open(io.BytesIO(data)))
        if target.exists() or any(distance(h, other) <= max_distance for other in seen):
            counts["duplicate"] += 1
            continue
        target.write_bytes(data)
        seen.append(h)
        rows.append({"file": target.name, "source": source, "license": license, "label": label, "split": split_for(sha)})
        counts["added"] += 1

    write_manifest(out / "manifest.csv", rows)
    return counts


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("src", type=Path)
    parser.add_argument("--out", type=Path, default=DATASETS_DIR)
    parser.add_argument("--source", required=True)
    parser.add_argument("--license", required=True)
    parser.add_argument("--label", default="")
    parser.add_argument("--n", type=int, default=50)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--max-distance", type=int, default=DEFAULT_MAX_DISTANCE)
    args = parser.parse_args(argv)
    counts = prepare(args.src, args.out, source=args.source, license=args.license, label=args.label, n=args.n,
                     seed=args.seed, max_distance=args.max_distance)
    print(counts)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
