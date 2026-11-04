"""Offline evaluation of the vision pipeline on the audited label set, logged to MLflow.

Usage (from backend/, needs the Meta Model API and `pip install -r requirements-eval.txt`):
  python -m scripts.evaluate [--root datasets] [--split eval|dev|all] [--run-name NAME]
  python -m scripts.evaluate --feedback-only        log pipeline agreement and agent score per prompt version

One MLflow run per configuration. Change the configuration with the usual settings (INSPECT_PROMPT, REVIEW_CONFIDENCE,
PRIVACY_BLUR, MUSE_MODEL ...) and run again; the MLflow UI (`mlflow ui --backend-store-uri <MLFLOW_TRACKING_URI>`) compares them.
Labels are image-level: damaged/intact judge the boxes, blocked/clear judge the walkway. Quarantined files are excluded.
Token counts and box-count error are not logged: the pipeline does not report tokens and the manifest holds no box counts.
"""

import argparse
import asyncio
import csv
import json
import math
import sys
import tempfile
import time
from pathlib import Path

from app import scoring
from app.config import BACKEND_DIR, get_settings
from app.pipeline import scan
from app.pipeline.hazards import BLOCK_THRESHOLD
from app.pipeline.inspect import prompt_version
from scripts.audit_labels import read_exclusions
from scripts.dataset_utils import DATASETS_DIR, LABELS, read_manifest

EXPERIMENT = "aisleeye-pipeline"


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, math.ceil(q * len(ordered)) - 1)]


def _ratio(num: int, den: int) -> float | None:
    return None if den == 0 else num / den


def compute_metrics(rows: list[dict]) -> dict:
    """Metrics from per-image rows: label, damaged_pred, blocked_pred, needs_review, latency_ms."""
    damage = [r for r in rows if r["label"] in ("damaged", "intact")]
    tp = sum(r["label"] == "damaged" and r["damaged_pred"] for r in damage)
    fp = sum(r["label"] == "intact" and r["damaged_pred"] for r in damage)
    fn = sum(r["label"] == "damaged" and not r["damaged_pred"] for r in damage)
    precision, recall = _ratio(tp, tp + fp), _ratio(tp, tp + fn)
    f1 = None if precision is None or recall is None or precision + recall == 0 else 2 * precision * recall / (precision + recall)
    hazard = [r for r in rows if r["label"] in ("blocked", "clear")]
    latencies = [r["latency_ms"] for r in rows]
    metrics = {
        "damage_precision": precision,
        "damage_recall": recall,
        "damage_f1": f1,
        "hazard_accuracy": _ratio(sum((r["label"] == "blocked") == r["blocked_pred"] for r in hazard), len(hazard)),
        "needs_review_rate": _ratio(sum(r["needs_review"] for r in rows), len(rows)),
        "latency_ms_p50": percentile(latencies, 0.5),
        "latency_ms_p95": percentile(latencies, 0.95),
        "images": len(rows),
        "damage_images": len(damage),
        "hazard_images": len(hazard),
    }
    return {k: v for k, v in metrics.items() if v is not None}


def confusion(rows: list[dict]) -> dict:
    out = {"damage": {"tp": 0, "fp": 0, "fn": 0, "tn": 0}, "hazard": {"tp": 0, "fp": 0, "fn": 0, "tn": 0}}
    for r in rows:
        if r["label"] in ("damaged", "intact"):
            actual, pred, key = r["label"] == "damaged", r["damaged_pred"], "damage"
        elif r["label"] in ("blocked", "clear"):
            actual, pred, key = r["label"] == "blocked", r["blocked_pred"], "hazard"
        else:
            continue
        out[key][("t" if actual == pred else "f") + ("p" if pred else "n")] += 1
    return out


def disagreements(rows: list[dict]) -> list[dict]:
    wrong = []
    for r in rows:
        if r["label"] in ("damaged", "intact") and (r["label"] == "damaged") != r["damaged_pred"]:
            wrong.append({"file": r["file"], "label": r["label"], "model_says": f"damaged={r['damaged_pred']}"})
        if r["label"] in ("blocked", "clear") and (r["label"] == "blocked") != r["blocked_pred"]:
            wrong.append({"file": r["file"], "label": r["label"], "model_says": f"blocked={r['blocked_pred']}"})
    return wrong


def select_rows(root: Path, split: str) -> list[dict]:
    excluded = read_exclusions(root)
    return [
        r for r in read_manifest(root / "manifest.csv")
        if r["file"] not in excluded and r["label"] in LABELS and split in ("all", r["split"])
    ]


async def evaluate_rows(root: Path, manifest_rows: list[dict]) -> list[dict]:
    results = []
    with tempfile.TemporaryDirectory() as tmp:
        scan.CACHE_DIR = Path(tmp)  # never reuse an older result: the configuration is what is being measured
        for r in manifest_rows:
            started = time.perf_counter()
            result = await scan.run_scan((root / "processed" / r["file"]).read_bytes(), "image/jpeg")
            results.append({
                "file": r["file"], "label": r["label"], "split": r["split"],
                "damaged_pred": any(f.type == "box_damage" for f in result.findings),
                "blocked_pred": any(f.type == "walkway_blocked" for f in result.findings),
                "boxes": sum(o.label == "box" for o in result.objects),
                "needs_review": any(f.needs_review for f in result.findings),
                "latency_ms": round((time.perf_counter() - started) * 1000),
            })
    return results


def configuration() -> dict:
    s = get_settings()
    return {
        "prompt_version": prompt_version(),
        "muse_model": s.muse_model,
        "sam_model": s.sam_model,
        "walkway_overlap_threshold": BLOCK_THRESHOLD,
        "walkway_min_confidence": s.walkway_min_confidence,
        "review_confidence": s.review_confidence,
        "privacy_blur": s.privacy_blur,
        "agent_runtime": s.agent_runtime,
    }


def feedback_metrics(lines: list[dict]) -> dict[str, float]:
    """Pipeline agreement and agent score per prompt version, from what reviewers and runs have recorded."""
    out = {}
    entries = scoring.latest_entries(lines)
    for version in sorted({e["prompt_version"] for e in entries if e.get("prompt_version")}):
        summary = scoring.summarize([e for e in entries if e.get("prompt_version") == version], min_reviews=1)
        for name, key in (("pipeline_human_agreement", "pipeline_agreement"), ("agent_score", "agent_score")):
            if summary[key]["value"] is not None:
                out[f"{name}.{version}"] = summary[key]["value"]
    return out


def _mlflow():
    try:
        import mlflow
    except ImportError:
        return None
    uri = get_settings().mlflow_tracking_uri
    if uri.startswith("sqlite:///") and not uri.startswith("sqlite:////"):
        db = BACKEND_DIR / uri.removeprefix("sqlite:///")  # relative paths mean backend/, wherever this runs from
        db.parent.mkdir(parents=True, exist_ok=True)
        uri = f"sqlite:///{db}"
    mlflow.set_tracking_uri(uri)
    if mlflow.get_experiment_by_name(EXPERIMENT) is None:
        artifacts = BACKEND_DIR / "data" / "mlflow" / "artifacts"
        artifacts.mkdir(parents=True, exist_ok=True)
        mlflow.create_experiment(EXPERIMENT, artifact_location=artifacts.as_uri())
    mlflow.set_experiment(EXPERIMENT)
    return mlflow


def _mlflow_or_notice():
    mlflow = _mlflow()
    if mlflow is None:
        print("mlflow is not installed: metrics are printed only. Install it with `pip install -r requirements-eval.txt`.")
    return mlflow


def _write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", type=Path, default=DATASETS_DIR)
    parser.add_argument("--split", choices=("eval", "dev", "all"), default="eval")
    parser.add_argument("--run-name")
    parser.add_argument("--feedback-only", action="store_true")
    args = parser.parse_args(argv)

    if args.feedback_only:
        mlflow = _mlflow_or_notice()
        metrics = feedback_metrics(scoring.read_lines())
        print(json.dumps(metrics, indent=1))
        if mlflow and metrics:
            with mlflow.start_run(run_name=args.run_name or "feedback"):
                mlflow.log_metrics(metrics)
        return 0

    manifest_rows = select_rows(args.root, args.split)
    if not manifest_rows:
        print(f"No labelled images in {args.root / 'manifest.csv'} for split {args.split!r}.")
        return 1
    mlflow = _mlflow_or_notice()
    rows = asyncio.run(evaluate_rows(args.root, manifest_rows))
    metrics, wrong = compute_metrics(rows), disagreements(rows)
    print(json.dumps({"config": configuration(), "metrics": metrics, "disagreements": len(wrong)}, indent=1))

    if mlflow:
        with mlflow.start_run(run_name=args.run_name), tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            mlflow.log_params({**configuration(), "split": args.split})
            mlflow.log_metrics(metrics)
            _write_csv(out / "results.csv", rows, list(rows[0]))
            _write_csv(out / "disagreements.csv", wrong, ["file", "label", "model_says"])
            (out / "confusion.json").write_text(json.dumps(confusion(rows), indent=1))
            mlflow.log_artifacts(str(out))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
