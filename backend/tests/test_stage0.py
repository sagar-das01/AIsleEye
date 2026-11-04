import base64
import io
import json
import sys
import types
from types import SimpleNamespace

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from PIL import Image
from pydantic import ValidationError

from app import clients
from app.config import ROOT_DIR, Settings
from scripts import smoke_test


@pytest.fixture
def out_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(smoke_test, "OUT_DIR", tmp_path)
    return tmp_path


def png_url(size=(100, 80)) -> str:
    buf = io.BytesIO()
    Image.new("RGB", size).save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


# ---- settings -------------------------------------------------------------------------------------------------

def test_planned_settings_have_the_documented_defaults():
    s = Settings(_env_file=None)
    assert (s.muse_reasoning_effort, s.agent_runtime, s.demo_mode, s.privacy_blur) == ("minimal", "langchain", "", True)
    assert (s.review_confidence, s.cache_ttl_days, s.guard_max_tool_calls, s.score_min_reviews) == (0.6, 7, 8, 5)
    assert s.langfuse_enabled is False and s.walkway_concept == "yellow line"
    assert s.score_weights == {"validity": 0.4, "guard_clean": 0.4, "within_cap": 0.2}
    assert s.mlflow_tracking_uri == "sqlite:///data/mlflow/mlflow.db"


@pytest.mark.parametrize("effort", ["", "minimal", "low", "medium", "high", "xhigh", "max"])
def test_every_effort_the_api_accepts_is_accepted(effort, monkeypatch):
    monkeypatch.setenv("MUSE_REASONING_EFFORT", effort)
    assert Settings(_env_file=None).muse_reasoning_effort == effort


@pytest.mark.parametrize("bad", ["none", "fast", "LOW"])
def test_efforts_the_api_rejects_are_rejected_early(monkeypatch, bad):
    monkeypatch.setenv("MUSE_REASONING_EFFORT", bad)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


@pytest.mark.parametrize("var", ["MUSE_MODEL", "SAM_MODEL", "MUSE_AGENT_MODEL"])
def test_contributor_mode_models_are_refused_because_photos_would_be_used_for_training(monkeypatch, var):
    monkeypatch.setenv(var, "muse-spark-1.3-contributor")
    with pytest.raises(ValidationError, match="Standard mode"):
        Settings(_env_file=None)


@pytest.mark.parametrize(
    ("var", "value"),
    [("REVIEW_CONFIDENCE", "1.5"), ("REVIEW_CONFIDENCE", "-0.1"), ("CACHE_TTL_DAYS", "0"), ("GUARD_MAX_TOOL_CALLS", "0"),
     ("SCORE_MIN_REVIEWS", "0"), ("AGENT_RUNTIME", "magic"), ("DEMO_MODE", "live")],
)
def test_out_of_range_settings_fail_at_startup(monkeypatch, var, value):
    monkeypatch.setenv(var, value)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_langfuse_secret_never_appears_in_reprs_or_dumps(monkeypatch):
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-super-secret")
    s = Settings(_env_file=None)
    assert "super-secret" not in repr(s) and "super-secret" not in s.model_dump_json()
    assert s.langfuse_secret_key.get_secret_value() == "sk-lf-super-secret"


def test_env_example_is_valid_and_matches_the_defaults(monkeypatch):
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)
    monkeypatch.setenv("MODEL_API_KEY", "k")
    from_example = Settings(_env_file=ROOT_DIR / ".env.example")
    assert from_example.model_dump() == Settings(_env_file=None).model_dump()


# ---- clients --------------------------------------------------------------------------------------------------

def test_reasoning_kwargs_follow_the_setting(monkeypatch):
    monkeypatch.setenv("MUSE_REASONING_EFFORT", "low")
    clients.get_settings.cache_clear()
    try:
        assert clients.reasoning_kwargs() == {"reasoning": {"effort": "low"}}
        monkeypatch.setenv("MUSE_REASONING_EFFORT", "")
        clients.get_settings.cache_clear()
        assert clients.reasoning_kwargs() == {}
    finally:
        monkeypatch.undo()
        clients.get_settings.cache_clear()


def test_chat_model_targets_the_meta_api_with_the_configured_model_and_effort():
    llm = clients.get_chat_model()
    assert llm.model_name == "muse-spark-1.3" and str(llm.openai_api_base).rstrip("/") == "https://api.meta.ai/v1"
    assert llm.reasoning_effort == "minimal" and llm.max_retries == 2
    assert clients.get_chat_model("muse-wonder-1.0").model_name == "muse-wonder-1.0"


# ---- smoke: SAM frame check -----------------------------------------------------------------------------------

def sam_reply(w, h):
    return SimpleNamespace(output_text=f"<0f>0<|box;x1=1;y1=1;x2=5;y2=5;w={w};h={h};c=0.9|><|mask;x=0;y=0;c=0.9;data=1,1,!|>")


def fake_sam(monkeypatch, reply):
    responses = SimpleNamespace(create=lambda **kw: reply)
    monkeypatch.setattr(smoke_test, "get_client", lambda: SimpleNamespace(responses=responses))


def test_sam_frame_equal_to_the_sent_image_passes(monkeypatch, out_dir):
    fake_sam(monkeypatch, sam_reply(100, 80))
    assert "1 boxes" in smoke_test.check_sam("sam", png_url())


def test_sam_frame_different_from_the_sent_image_fails_loudly(monkeypatch, out_dir):
    fake_sam(monkeypatch, sam_reply(200, 160))
    with pytest.raises(AssertionError, match="scale coordinates"):
        smoke_test.check_sam("sam", png_url())


# ---- smoke: LangChain agent -----------------------------------------------------------------------------------

class ToolFake(GenericFakeChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


def scripted_llm(*messages):
    return ToolFake(messages=iter(messages))


def tool_call(name="adjust_inventory", **args):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args or {"sku": "SKU-1042", "delta": 1, "reason": "x"}, "id": "c1"}])


def test_langchain_agent_check_passes_on_tool_call_then_answer(monkeypatch):
    monkeypatch.setattr(smoke_test, "get_chat_model", lambda model=None: scripted_llm(tool_call(), AIMessage(content="Quarantined 1 unit.")))
    assert "SKU-1042" in smoke_test.check_langchain_agent("m", "")


def test_langchain_agent_check_fails_when_the_model_never_calls_the_tool(monkeypatch):
    monkeypatch.setattr(smoke_test, "get_chat_model", lambda model=None: scripted_llm(AIMessage(content="I would rather not.")))
    with pytest.raises(AssertionError, match="without calling the tool"):
        smoke_test.check_langchain_agent("m", "")


def test_langchain_agent_check_fails_without_a_final_answer(monkeypatch):
    monkeypatch.setattr(smoke_test, "get_chat_model", lambda model=None: scripted_llm(tool_call(), AIMessage(content="")))
    with pytest.raises(AssertionError, match="final text"):
        smoke_test.check_langchain_agent("m", "")


# ---- smoke: Langfuse ------------------------------------------------------------------------------------------

def test_langfuse_check_is_skipped_when_not_configured(monkeypatch):
    for var in ("LANGFUSE_ENABLED", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"):
        monkeypatch.setenv(var, "")  # an empty real env var beats any value in the local .env file
    monkeypatch.setenv("LANGFUSE_ENABLED", "false")
    smoke_test.get_settings.cache_clear()
    try:
        with pytest.raises(smoke_test.Skip):
            smoke_test.check_langfuse("m", "")
    finally:
        smoke_test.get_settings.cache_clear()


def install_fake_langfuse(monkeypatch, *, auth_ok=True):
    events = []

    class FakeLangfuse:
        def __init__(self, **kwargs):
            events.append(("init", kwargs["base_url"]))

        def auth_check(self):
            return auth_ok

        def flush(self):
            events.append(("flush",))

    module = types.ModuleType("langfuse")
    module.Langfuse = FakeLangfuse
    lc = types.ModuleType("langfuse.langchain")
    lc.CallbackHandler = lambda: "handler"
    monkeypatch.setitem(sys.modules, "langfuse", module)
    monkeypatch.setitem(sys.modules, "langfuse.langchain", lc)
    import truststore

    monkeypatch.setattr(truststore, "inject_into_ssl", lambda: events.append(("tls",)))
    calls = []
    llm = SimpleNamespace(invoke=lambda prompt, config=None: calls.append(config))
    monkeypatch.setattr(smoke_test, "get_chat_model", lambda model=None: llm)
    monkeypatch.setenv("LANGFUSE_ENABLED", "true")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk")
    smoke_test.get_settings.cache_clear()
    return events, calls


def test_langfuse_check_authenticates_traces_and_flushes(monkeypatch):
    events, calls = install_fake_langfuse(monkeypatch)
    try:
        assert "trace sent" in smoke_test.check_langfuse("m", "")
    finally:
        monkeypatch.undo()
        smoke_test.get_settings.cache_clear()
    assert ("tls",) in events and ("flush",) in events and calls == [{"callbacks": ["handler"]}]


def test_langfuse_check_fails_on_rejected_keys(monkeypatch):
    install_fake_langfuse(monkeypatch, auth_ok=False)
    try:
        with pytest.raises(AssertionError, match="rejected the keys"):
            smoke_test.check_langfuse("m", "")
    finally:
        monkeypatch.undo()
        smoke_test.get_settings.cache_clear()


# ---- smoke: main() and latency --------------------------------------------------------------------------------

def test_skipped_checks_do_not_fail_the_run_and_are_reported(monkeypatch, out_dir, capsys):
    monkeypatch.setattr("sys.argv", ["smoke_test"])
    for name in ("check_text", "check_vision", "check_structured", "check_tools", "check_sam", "check_langchain_agent"):
        monkeypatch.setattr(smoke_test, name, lambda m, u: "ok")

    def skip(m, u):
        raise smoke_test.Skip("not configured")

    monkeypatch.setattr(smoke_test, "check_langfuse", skip)
    assert smoke_test.main() == 0
    out = capsys.readouterr().out
    assert "SKIP" in out and "6/6 passed (1 skipped)" in out
    report = json.loads((out_dir / "report.json").read_text())
    assert [r["status"] for r in report] == ["PASS"] * 6 + ["SKIP"]


def fake_latency_client(monkeypatch):
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(output_text="x", usage=SimpleNamespace(output_tokens=7))

    monkeypatch.setattr(smoke_test, "get_client", lambda: SimpleNamespace(responses=SimpleNamespace(create=create)))
    return calls


def test_latency_baseline_times_vision_and_structured_per_effort(monkeypatch):
    calls = fake_latency_client(monkeypatch)
    rows = smoke_test.measure_latency("data:image/png;base64,AA", efforts=[None, "minimal", "high"])
    assert [(r["call"], r["effort"]) for r in rows] == [
        ("vision", "default"), ("structured", "default"), ("vision", "minimal"), ("structured", "minimal"),
        ("vision", "high"), ("structured", "high"),
    ]
    assert all(isinstance(r["seconds"], float) and r["output_tokens"] == 7 for r in rows)
    assert "reasoning" not in calls[0] and calls[2]["reasoning"] == {"effort": "minimal"}
    assert "text" in calls[1] and "text" not in calls[0]


def test_latency_mode_writes_a_report_and_skips_the_checks(monkeypatch, out_dir, capsys):
    fake_latency_client(monkeypatch)
    monkeypatch.setattr("sys.argv", ["smoke_test", "--latency"])
    monkeypatch.setattr(smoke_test, "measure_latency", lambda url: [{"call": "vision", "effort": "minimal", "seconds": 1.0, "output_tokens": 3}])
    assert smoke_test.main() == 0
    assert json.loads((out_dir / "latency.json").read_text())[0]["effort"] == "minimal"
    assert "PASS" not in capsys.readouterr().out
