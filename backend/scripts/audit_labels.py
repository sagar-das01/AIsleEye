"""Audit the evaluation labels before they count toward any metric.

Usage (from backend/):
  python -m scripts.audit_labels                       offline checks -> datasets/review/issues.csv
  python -m scripts.audit_labels --with-models         also run the pipeline and list label/model disagreements
  python -m scripts.audit_labels --quarantine FILE...  move confirmed mislabels aside and exclude them from evaluation

Checks: manifest schema, valid labels and splits, every file present and named after its hash, class balance per split,
and near-duplicates that sit in different splits (they would leak the answer into the evaluation).
A person decides what is a real mislabel; the models only point at candidates.
"""

import argparse
import asyncio
import csv
import hashlib
import shutil
import sys
import tempfile
from collections import Counter
from pathlib import Path

from PIL import Image

from scripts.dataset_utils import (
    DATASETS_DIR,
    DEFAULT_MAX_DISTANCE,
    FIELDS,
    LABELS,
    SPLITS,
    dhash,
    distance,
    read_manifest,
)

EXCLUSIONS = "exclusions.txt"


def read_exclusions(root: Path) -> set[str]:
    path = root / EXCLUSIONS
    return {line.strip() for line in path.read_text().splitlines() if line.strip()} if path.is_file() else set()


def audit(root: Path, *, max_distance: int = DEFAULT_MAX_DISTANCE) -> tuple[list[dict], dict]:
    """Return (issues, class balance per split). Excluded files are skipped."""
    manifest = root / "manifest.csv"
    issues: list[dict] = []
    if manifest.is_file() and set(FIELDS) - set(manifest.read_text().splitlines()[0].split(",")):
        return [{"file": "manifest.csv", "issue": f"manifest must have the columns {FIELDS}"}], {}
    excluded = read_exclusions(root)
    rows = [r for r in read_manifest(manifest) if r["file"] not in excluded]
    balance: dict[str, Counter] = {s: Counter() for s in SPLITS}
    hashes: dict[str, int] = {}
    for r in rows:
        name = r["file"]
        path = root / "processed" / name
        if r["label"] not in LABELS:
            issues.append({"file": name, "issue": f"label {r['label']!r} is not one of {list(LABELS)}"})
        if r["split"] not in SPLITS:
            issues.append({"file": name, "issue": f"split {r['split']!r} is not one of {list(SPLITS)}"})
        if not r["license"].strip():
            issues.append({"file": name, "issue": "no license recorded"})
        if not path.is_file():
            issues.append({"file": name, "issue": "file is missing"})
            continue
        if hashlib.sha256(path.read_bytes()).hexdigest()[:16] != Path(name).stem:
            issues.append({"file": name, "issue": "file content does not match its hash name"})
        if r["label"] in LABELS and r["split"] in SPLITS:
            balance[r["split"]][r["label"]] += 1
        try:
            hashes[name] = dhash(Image.open(path))
        except (OSError, SyntaxError):
            issues.append({"file": name, "issue": "file is not a readable image"})

    split_of = {r["file"]: r["split"] for r in rows}
    names = sorted(hashes)
    for i, a in enumerate(names):
        for b in names[i + 1 :]:
            if split_of[a] != split_of[b] and distance(hashes[a], hashes[b]) <= max_distance:
                issues.append({"file": a, "issue": f"near-duplicate of {b} in another split ({split_of[a]} vs {split_of[b]})"})
    return issues, {s: dict(c) for s, c in balance.items()}


def disagreements(root: Path) -> list[dict]:
    """Run the live pipeline on every labelled image and list where it contradicts the label."""
    from app.pipeline import scan

    excluded = read_exclusions(root)
    rows = [r for r in read_manifest(root / "manifest.csv") if r["file"] not in excluded and r["label"] in LABELS]
    out: list[dict] = []

    async def run() -> None:
        for r in rows:
            result = await scan.run_scan((root / "processed" / r["file"]).read_bytes(), "image/jpeg")
            damaged = any(f.type == "box_damage" for f in result.findings)
            blocked = any(f.type == "walkway_blocked" for f in result.findings)
            seen = {"damaged": damaged, "intact": not damaged, "blocked": blocked, "clear": not blocked}[r["label"]]
            if not seen:
                out.append({"file": r["file"], "label": r["label"], "model_says": f"damaged={damaged}, blocked={blocked}"})

    with tempfile.TemporaryDirectory() as tmp:
        scan.CACHE_DIR = Path(tmp)
        asyncio.run(run())  # one loop: the cached async client is bound to it
    return out


def quarantine(root: Path, files: list[str]) -> list[str]:
    """Move confirmed mislabels out of processed/ and add them to the exclusion list the evaluation reads."""
    known = {r["file"] for r in read_manifest(root / "manifest.csv")}
    moved = []
    (root / "quarantine").mkdir(parents=True, exist_ok=True)
    exclusions = read_exclusions(root)
    for name in files:
        if name not in known or Path(name).name != name:
            raise ValueError(f"{name!r} is not in the manifest")
        src = root / "processed" / name
        if src.is_file():
            shutil.move(src, root / "quarantine" / name)
        exclusions.add(name)
        moved.append(name)
    (root / EXCLUSIONS).write_text("\n".join(sorted(exclusions)) + "\n")
    return moved


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", type=Path, default=DATASETS_DIR)
    parser.add_argument("--with-models", action="store_true")
    parser.add_argument("--quarantine", nargs="+", metavar="FILE")
    parser.add_argument("--max-distance", type=int, default=DEFAULT_MAX_DISTANCE)
    args = parser.parse_args(argv)

    if args.quarantine:
        print("quarantined:", quarantine(args.root, args.quarantine))
        return 0
    issues, balance = audit(args.root, max_distance=args.max_distance)
    write_csv(args.root / "review" / "issues.csv", issues, ["file", "issue"])
    print("class balance:", balance)
    print(f"{len(issues)} issue(s) -> {args.root / 'review' / 'issues.csv'}")
    if args.with_models:
        found = disagreements(args.root)
        write_csv(args.root / "review" / "disagreements.csv", found, ["file", "label", "model_says"])
        print(f"{len(found)} disagreement(s) -> {args.root / 'review' / 'disagreements.csv'}")
    return 1 if issues else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
