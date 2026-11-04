import io
import json
import re
import uuid

import numpy as np
import pytest
from fakes import concept_of, install_fake_model, sam_line, sam_record
from fastapi.testclient import TestClient
from PIL import Image

import app.agent.orchestrator as orch
import app.agent.tools as tools
import app.audit as audit
import app.config as config
import app.feedback as feedback
from app import main, observability, replay, retention, scoring
from app.pipeline import scan as scan_mod

W, H = 100, 80
BOX = (20, 30, 49, 59)
client = TestClient(main.app)
TICKET = {"finding_id": "dmg_box_0", "title": "Damaged box", "severity": "low", "assignee_team": "Maintenance", "description": "Dent"}


@pytest.fixture(autouse=True)
def world(tmp_path, monkeypatch):
    monkeypatch.setattr(scan_mod, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(feedback, "FEEDBACK_PATH", tmp_path / "feedback.jsonl")
    monkeypatch.setattr(replay, "DEMO_DIR", tmp_path / "demo")
    monkeypatch.setattr(retention, "DEMO_DIR", tmp_path / "demo")
    monkeypatch.setattr(tools, "DATA_DIR", tmp_path)
    monkeypatch.setattr(audit, "AUDIT_PATH", tmp_path / "audit_log.jsonl")
    (tmp_path / "inventory.json").write_text('{"SKU-1": {"available": 10, "quarantine": 0}}')
    monkeypatch.setenv("DEMO_MODE", "")
    monkeypatch.setenv("AGENT_RUNTIME", "plain")
    config.get_settings.cache_clear()
    return tmp_path


@pytest.fixture
def exporter(monkeypatch):
    from langfuse import Langfuse
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    memory = InMemorySpanExporter()
    lf = Langfuse(
        public_key=f"pk-{uuid.uuid4().hex}", secret_key="sk", base_url="http://127.0.0.1:9", span_exporter=memory,
        mask=observability._mask_for_langfuse, mask_otel_spans=observability._mask_otel_spans, flush_at=1,
    )
    monkeypatch.setattr(observability, "_client", lf)
    return lambda: (lf.flush(), memory.get_finished_spans())[1]


def noisy_png() -> bytes:
    rng = np.random.default_rng(3)
    buf = io.BytesIO()
    Image.fromarray(rng.integers(0, 256, (H, W, 3), dtype=np.uint8)).save(buf, format="PNG")
    return buf.getvalue()


def model(kw):
    if kw["model"] == "sam-3.1":
        concept = concept_of(kw)
        if concept == "cardboard box":
            return sam_line(sam_record(0, *BOX, w=W, h=H, c=0.95))
        return sam_line(sam_record(0, 0, 50, 99, 59, w=W, h=H, c=0.8)) if concept == "yellow line" else sam_line()
    return json.dumps({"assessments": [{"index": 0, "damaged": True, "damage_type": "dent", "severity": "low", "confidence": 0.9, "reason": "Dent"}]})


def scan() -> dict:
    res = client.post("/scan", files={"image": ("p.png", noisy_png(), "image/png")})
    assert res.status_code == 200, res.text
    return res.json()


def approve(scan_id: str, ids: list[str], step) -> orch.RunResult:
    return orch.run_approved_scan(replay.find_scan(scan_id), ids, step=step)


def once(*calls):
    queue = [list(calls)]
    return lambda messages: queue.pop(0) if queue else []


def names(spans) -> set[str]:
    return {s.name for s in spans}


# ---- one trace, a span for every stage ---------------------------------------------------------------------------

def test_scan_and_approval_land_in_one_trace_with_a_span_per_stage(exporter, monkeypatch):
    install_fake_model(monkeypatch, model)
    body = scan()
    finding = body["findings"][0]["id"]
    run = approve(body["scan_id"], [finding], once(orch.ToolCall("create_incident_ticket", {**TICKET, "finding_id": finding})))
    spans = exporter()
    assert {"scan", "preprocess", "sam:box", "sam:walkway", "sam:person", "privacy_blur", "inspect_boxes", "hazards"} <= names(spans)
    assert {"approve", "agent_run", "tool:create_incident_ticket", "guard"} <= names(spans)
    trace = observability.trace_id_for(body["scan_id"])
    assert {format(s.context.trace_id, "032x") for s in spans} == {trace} and run.trace_id == trace
    assert {s.attributes.get("session.id") for s in spans if s.name in {"scan", "approve"}} == {body["scan_id"]}


def test_a_rejected_call_shows_up_as_a_guard_verdict_and_a_warning(exporter, monkeypatch):
    install_fake_model(monkeypatch, model)
    body = scan()
    run = approve(body["scan_id"], [body["findings"][0]["id"]], once(orch.ToolCall("create_incident_ticket", {**TICKET, "finding_id": "not_approved"})))
    assert [a.status for a in run.actions] == ["rejected"]
    spans = exporter()
    guard = next(s for s in spans if s.name == "guard")
    assert '"allowed": false' in guard.attributes["langfuse.observation.output"]
    tool = next(s for s in spans if s.name.startswith("tool:"))
    assert tool.attributes["langfuse.observation.level"] == "WARNING"


def test_spans_carry_the_model_names_and_thresholds(exporter, monkeypatch):
    install_fake_model(monkeypatch, model)
    scan()
    root = next(s for s in exporter() if s.name == "scan")
    meta = {k.split(".")[-1]: json.loads(v) for k, v in root.attributes.items() if "metadata" in k}
    assert meta["sam_model"] == "sam-3.1" and meta["prompt_version"] == "inspect-v1" and meta["review_confidence"] == 0.6


# ---- no image data in any trace ----------------------------------------------------------------------------------

def test_no_exported_span_holds_image_data(exporter, monkeypatch):
    install_fake_model(monkeypatch, model)
    body = scan()
    approve(body["scan_id"], [body["findings"][0]["id"]], once())
    with observability.span("probe", as_type="generation", input={"url": "data:image/jpeg;base64," + "A" * 3000}, crop=b"\x89PNG" * 800) as obs:
        observability.update(obs, output="data:image/png;base64," + "B" * 3000)
    for s in exporter():
        for value in s.attributes.values():
            text = str(value)
            assert "data:image" not in text and "langfuseMedia" not in text and not re.search(r"[A-Za-z0-9+/=]{200,}", text), s.name


def test_the_export_backstop_also_cleans_spans_we_did_not_write(exporter):
    from langfuse.types import MaskOtelSpansParams, OtelSpanData

    span = OtelSpanData(
        trace_id="a" * 32, span_id="b" * 16, parent_span_id=None, name="third-party", instrumentation_scope_name="x",
        instrumentation_scope_version=None, attributes={"gen_ai.prompt": "see data:image/png;base64," + "C" * 400, "n": 3}, resource_attributes={},
    )
    result = observability._mask_otel_spans(params=MaskOtelSpansParams(spans={"k": span}))
    patch = result.span_patches["k"]
    assert "data:image" not in patch.set_attributes["gen_ai.prompt"] and "n" not in patch.set_attributes


# ---- tracing must never change the outcome -----------------------------------------------------------------------

def test_a_broken_tracer_does_not_change_scan_or_approval(monkeypatch):
    install_fake_model(monkeypatch, model)
    baseline = scan()
    boom = type("Boom", (), {"__getattr__": lambda self, name: (_ for _ in ()).throw(RuntimeError("tracer down"))})()
    monkeypatch.setattr(observability, "_client", boom)
    scan_mod.CACHE_DIR.joinpath(f"{baseline['scan_id']}.json").unlink()
    again = scan()
    assert again["findings"] == baseline["findings"]
    run = approve(again["scan_id"], [again["findings"][0]["id"]], once(orch.ToolCall("create_incident_ticket", {**TICKET, "finding_id": again["findings"][0]["id"]})))
    assert [a.status for a in run.actions] == ["executed"]


def test_a_scoring_failure_does_not_fail_the_run(monkeypatch):
    install_fake_model(monkeypatch, model)
    body = scan()
    monkeypatch.setattr(scoring, "record_scores", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    run = approve(body["scan_id"], [body["findings"][0]["id"]], once(orch.ToolCall("create_incident_ticket", {**TICKET, "finding_id": body["findings"][0]["id"]})))
    assert [a.status for a in run.actions] == ["executed"]


# ---- scores --------------------------------------------------------------------------------------------------------

def lines():
    return [json.loads(line) for line in scoring.SCORES_PATH.read_text().splitlines()]


def test_every_agent_run_records_automatic_scores(monkeypatch):
    install_fake_model(monkeypatch, model)
    body = scan()
    fid = body["findings"][0]["id"]
    approve(body["scan_id"], [fid], once(orch.ToolCall("create_incident_ticket", {**TICKET, "finding_id": fid})))
    approve(body["scan_id"], [fid], once(
        orch.ToolCall("create_incident_ticket", {**TICKET, "finding_id": fid}),
        orch.ToolCall("delete_everything", {}),
        orch.ToolCall("log_compliance_event", {"finding_id": fid}),
    ))
    agent = [line for line in lines() if "agent_score" in line["scores"]][-1]
    s = agent["scores"]
    # A duplicate of the open ticket is a valid call the guard refuses; an unknown tool and bad arguments are invalid.
    assert s["tool_call_validity"] == pytest.approx(1 / 3)
    assert s["guard_rejections"] == 2 and s["duplicate_avoided"] == 1 and s["within_iteration_cap"] is True
    assert s["agent_score"] == pytest.approx(0.4 / 3 + 0.0 + 0.2)
    assert agent["prompt_version"] == "agent-v1" and agent["runtime"] == "plain" and s["latency_ms"] >= 0


def test_rules_runs_are_not_scored_as_an_agent(monkeypatch):
    monkeypatch.setenv("AGENT_RUNTIME", "rules")
    config.get_settings.cache_clear()
    install_fake_model(monkeypatch, model)
    body = scan()
    run = orch.run_approved_scan(replay.find_scan(body["scan_id"]), [body["findings"][0]["id"]])
    assert run.runtime == "rules"
    scores = [line["scores"] for line in lines() if "latency_ms" in line["scores"]][0]
    assert "agent_score" not in scores and "tool_call_validity" not in scores


def test_approving_scores_the_pipeline_by_reviewer_agreement(monkeypatch):
    install_fake_model(monkeypatch, model)
    body = scan()
    ids = [f["id"] for f in body["findings"]]
    approve(body["scan_id"], ids, once())
    review = [line for line in lines() if "pipeline_human_agreement" in line["scores"]][-1]
    assert review["scores"]["pipeline_human_agreement"] == 1.0 and review["n"]["pipeline_human_agreement"] == len(ids)
    assert review["prompt_version"] == "inspect-v1"


def test_a_rejection_lowers_agreement_and_resends_the_same_score(world, monkeypatch):
    install_fake_model(monkeypatch, model)
    sent = []
    monkeypatch.setattr(observability, "create_score", lambda scan_id, name, value, score_id, comment=None: sent.append((name, value, score_id)))
    body = scan()
    ids = [f["id"] for f in body["findings"]]
    assert len(ids) >= 2
    approve(body["scan_id"], ids[:1], once())
    res = client.post(f"/scan/{body['scan_id']}/feedback", json={"finding_id": ids[1], "action": "reject", "reason": "shadow"})
    assert res.status_code == 200
    agreement = [(v, i) for n, v, i in sent if n == "pipeline_human_agreement"]
    assert [v for v, _ in agreement] == [1.0, 0.5]
    assert len({i for _, i in agreement}) == 1 and agreement[0][1] == f"{body['scan_id']}-pipeline_human_agreement"
    assert ("pipeline_review_reason", "shadow", f"{body['scan_id']}-pipeline_review_reason") in sent


def test_a_correction_counts_as_a_miss(monkeypatch):
    install_fake_model(monkeypatch, model)
    body = scan()
    ids = [f["id"] for f in body["findings"]]
    approve(body["scan_id"], ids[:1], once())
    client.post(f"/scan/{body['scan_id']}/feedback", json={"finding_id": ids[1], "action": "correct", "reason": "x", "corrected_type": "box_damage"})
    assert scoring.report(1)["pipeline_agreement"]["value"] == pytest.approx(0.5)


def test_scores_endpoint_hides_values_below_the_minimum_sample(monkeypatch):
    install_fake_model(monkeypatch, model)
    body = scan()
    approve(body["scan_id"], [f["id"] for f in body["findings"]], once(orch.ToolCall("create_incident_ticket", {**TICKET, "finding_id": body["findings"][0]["id"]})))
    out = client.get("/scores").json()
    assert out["pipeline_agreement"]["value"] is None and "so far" in out["pipeline_agreement"]["label"]
    assert out["agent_score"]["n"] == 1 and out["agent_score"]["value"] is None
    assert set(out["by_prompt_version"]) == {"agent-v1", "inspect-v1"}
    assert out["components"]["tool_call_validity"] == 1.0


def test_scores_endpoint_shows_values_once_enough_reviews_exist(monkeypatch):
    monkeypatch.setenv("SCORE_MIN_REVIEWS", "1")
    config.get_settings.cache_clear()
    install_fake_model(monkeypatch, model)
    body = scan()
    approve(body["scan_id"], [f["id"] for f in body["findings"]], once())
    card = client.get("/scores").json()["pipeline_agreement"]
    assert card["value"] == 1.0 and card["label"].startswith("100%")


def test_a_re_sent_score_replaces_the_old_one_in_the_report():
    scoring.record_scores("a" * 16, {"agent_score": 0.2}, prompt_version="agent-v1")
    scoring.record_scores("a" * 16, {"agent_score": 0.8}, prompt_version="agent-v1")
    scoring.record_scores("b" * 16, {"agent_score": 0.4}, prompt_version="agent-v1")
    card = scoring.report(1)["agent_score"]
    assert card["n"] == 2 and card["value"] == pytest.approx(0.6)


def test_no_scores_means_empty_cards_not_an_error():
    out = client.get("/scores").json()
    assert out["pipeline_agreement"]["n"] == 0 and out["agent_score"]["value"] is None and out["by_prompt_version"] == {}
