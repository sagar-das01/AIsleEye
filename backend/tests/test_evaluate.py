import json

import pytest

import app.config as config
from app import scoring
from app.pipeline import scan as scan_mod
from app.schemas import BBox, DetectedObject, Finding, ImageSize, ScanResult
from scripts import evaluate
from scripts.dataset_utils import write_manifest


def row(label, damaged=False, blocked=False, review=False, ms=100, file="f.jpg"):
    return {"file": file, "label": label, "split": "eval", "damaged_pred": damaged, "blocked_pred": blocked, "needs_review": review, "latency_ms": ms, "boxes": 1}


def test_damage_precision_recall_and_f1():
    rows = [row("damaged", damaged=True), row("damaged", damaged=False), row("intact", damaged=True), row("intact"), row("intact")]
    m = evaluate.compute_metrics(rows)
    assert m["damage_precision"] == pytest.approx(0.5) and m["damage_recall"] == pytest.approx(0.5) and m["damage_f1"] == pytest.approx(0.5)


def test_hazard_accuracy_only_counts_hazard_labels():
    rows = [row("blocked", blocked=True), row("clear", blocked=True), row("clear"), row("damaged", damaged=True, blocked=True)]
    m = evaluate.compute_metrics(rows)
    assert m["hazard_accuracy"] == pytest.approx(2 / 3) and m["hazard_images"] == 3 and m["damage_images"] == 1


def test_metrics_that_cannot_be_computed_are_left_out_rather_than_zero():
    m = evaluate.compute_metrics([row("blocked", blocked=True)])
    assert "damage_precision" not in m and "damage_recall" not in m and "damage_f1" not in m
    only_misses = evaluate.compute_metrics([row("damaged", damaged=False)])
    assert only_misses["damage_recall"] == 0.0 and "damage_precision" not in only_misses and "damage_f1" not in only_misses


def test_needs_review_rate_and_latency_percentiles():
    rows = [row("clear", review=i < 2, ms=ms) for i, ms in enumerate([100, 200, 300, 400, 1000])]
    m = evaluate.compute_metrics(rows)
    assert m["needs_review_rate"] == pytest.approx(0.4) and m["latency_ms_p50"] == 300 and m["latency_ms_p95"] == 1000


def test_empty_input_gives_only_counts():
    assert evaluate.compute_metrics([]) == {"images": 0, "damage_images": 0, "hazard_images": 0}


def test_confusion_and_disagreements_agree():
    rows = [row("damaged", damaged=True, file="a"), row("damaged", file="b"), row("intact", damaged=True, file="c"), row("clear", file="d"), row("blocked", file="e")]
    c = evaluate.confusion(rows)
    assert c["damage"] == {"tp": 1, "fp": 1, "fn": 1, "tn": 0} and c["hazard"] == {"tp": 0, "fp": 0, "fn": 1, "tn": 1}
    assert [d["file"] for d in evaluate.disagreements(rows)] == ["b", "c", "e"]


def test_split_selection_skips_unlabelled_and_excluded_images(tmp_path):
    write_manifest(tmp_path / "manifest.csv", [
        {"file": "a.jpg", "label": "damaged", "split": "eval"},
        {"file": "b.jpg", "label": "intact", "split": "dev"},
        {"file": "c.jpg", "label": "", "split": "eval"},
        {"file": "d.jpg", "label": "clear", "split": "eval"},
    ])
    (tmp_path / "exclusions.txt").write_text("d.jpg\n")
    assert [r["file"] for r in evaluate.select_rows(tmp_path, "eval")] == ["a.jpg"]
    assert [r["file"] for r in evaluate.select_rows(tmp_path, "all")] == ["a.jpg", "b.jpg"]


def test_evaluating_rows_runs_the_pipeline_per_image_without_reusing_the_cache(tmp_path, monkeypatch):
    (tmp_path / "processed").mkdir()
    (tmp_path / "processed" / "a.jpg").write_bytes(b"one")
    seen = []

    async def fake_run_scan(data, mime):
        seen.append(scan_mod.CACHE_DIR)
        flagged = Finding(id="hz_box_1", type="walkway_blocked", severity="low", confidence=0.4, bbox=BBox(x1=0, y1=0, x2=1, y2=1), description="d", needs_review=True)
        return ScanResult(scan_id="0" * 16, image_size=ImageSize(width=1, height=1), timings_ms={}, findings=[flagged],
                          objects=[DetectedObject(id="box_1", label="box", bbox=BBox(x1=0, y1=0, x2=1, y2=1), confidence=0.9, mask_svg="")])

    monkeypatch.setattr(scan_mod, "run_scan", fake_run_scan)
    monkeypatch.setattr(scan_mod, "CACHE_DIR", tmp_path / "real-cache")
    import asyncio

    [out] = asyncio.run(evaluate.evaluate_rows(tmp_path, [{"file": "a.jpg", "label": "blocked", "split": "eval"}]))
    assert out["blocked_pred"] and not out["damaged_pred"] and out["needs_review"] and out["boxes"] == 1
    assert seen[0] != tmp_path / "real-cache"


def test_configuration_reports_what_a_run_depends_on(monkeypatch):
    monkeypatch.setenv("INSPECT_PROMPT", "v2")
    monkeypatch.setenv("PRIVACY_BLUR", "false")
    config.get_settings.cache_clear()
    cfg = evaluate.configuration()
    assert cfg["prompt_version"] == "inspect-v2" and cfg["privacy_blur"] is False and cfg["walkway_overlap_threshold"] == 0.15


def test_feedback_metrics_report_the_latest_scores_per_prompt_version():
    lines = [
        {"scan_id": "a" * 16, "prompt_version": "inspect-v1", "scores": {"pipeline_human_agreement": 0.5}, "n": {"pipeline_human_agreement": 2}},
        {"scan_id": "a" * 16, "prompt_version": "inspect-v1", "scores": {"pipeline_human_agreement": 1.0}, "n": {"pipeline_human_agreement": 2}},
        {"scan_id": "b" * 16, "prompt_version": "inspect-v2", "scores": {"pipeline_human_agreement": 0.25}, "n": {"pipeline_human_agreement": 4}},
        {"scan_id": "c" * 16, "prompt_version": "agent-v1", "scores": {"agent_score": 0.9}},
    ]
    assert evaluate.feedback_metrics(lines) == {
        "pipeline_human_agreement.inspect-v1": 1.0, "pipeline_human_agreement.inspect-v2": 0.25, "agent_score.agent-v1": 0.9,
    }


def test_main_without_a_labelled_set_explains_and_fails(tmp_path, capsys):
    assert evaluate.main(["--root", str(tmp_path)]) == 1
    assert "No labelled images" in capsys.readouterr().out


def test_feedback_only_run_logs_to_mlflow(tmp_path, monkeypatch):
    pytest.importorskip("mlflow")
    monkeypatch.setenv("MLFLOW_TRACKING_URI", f"sqlite:///{tmp_path / 'mlflow.db'}")
    monkeypatch.setenv("MLFLOW_DISABLE_AGENT_HINT", "1")
    config.get_settings.cache_clear()
    monkeypatch.setattr(evaluate, "BACKEND_DIR", tmp_path)
    scoring.record_scores("a" * 16, {"agent_score": 0.8}, prompt_version="agent-v1")
    assert evaluate.main(["--feedback-only", "--run-name", "fb"]) == 0
    import mlflow

    run = mlflow.search_runs(experiment_names=[evaluate.EXPERIMENT], output_format="list")[0]
    assert run.data.metrics == {"agent_score.agent-v1": 0.8}


def test_a_full_run_logs_params_metrics_and_artifacts(tmp_path, monkeypatch):
    pytest.importorskip("mlflow")
    import mlflow

    monkeypatch.setenv("MLFLOW_TRACKING_URI", f"sqlite:///{tmp_path / 'mlflow.db'}")
    monkeypatch.setenv("MLFLOW_DISABLE_AGENT_HINT", "1")
    config.get_settings.cache_clear()
    monkeypatch.setattr(evaluate, "BACKEND_DIR", tmp_path)

    async def fake_rows(root, manifest_rows):
        return [row("damaged", damaged=True, file="a.jpg"), row("intact", damaged=True, file="b.jpg")]

    monkeypatch.setattr(evaluate, "evaluate_rows", fake_rows)
    write_manifest(tmp_path / "ds" / "manifest.csv", [
        {"file": "a.jpg", "label": "damaged", "split": "eval"}, {"file": "b.jpg", "label": "intact", "split": "eval"},
    ])
    assert evaluate.main(["--root", str(tmp_path / "ds"), "--run-name", "r1"]) == 0
    run = mlflow.search_runs(experiment_names=[evaluate.EXPERIMENT], output_format="list")[0]
    assert run.data.params["prompt_version"] == "inspect-v1" and run.data.metrics["damage_precision"] == 0.5
    names = {a.path for a in mlflow.artifacts.list_artifacts(run_id=run.info.run_id)} if hasattr(mlflow, "artifacts") else set()
    assert {"results.csv", "disagreements.csv", "confusion.json"} <= names
    assert json.loads(open(mlflow.artifacts.download_artifacts(run_id=run.info.run_id, artifact_path="confusion.json")).read())["damage"]["fp"] == 1
