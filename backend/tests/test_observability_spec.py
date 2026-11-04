"""Spec for app/observability.py (Phase 6 MUST + Phase 7 failure drills). Langfuse is optional and fail-safe.

Planned interface:
    init() -> bool                         True only when enabled AND keys are present; never raises
    trace_id_for(scan_id) -> str           deterministic 32-hex id (Langfuse.create_trace_id(seed=scan_id) when available)
    mask(value) -> value                   recursively drops image data: data: URLs and long base64 blobs
    span(name, **attrs)                    context manager; no-op when tracing is off; swallows tracing errors only
    shutdown() -> None                     flush, never raises
"""

import base64
import os
import re

import pytest
from spec_support import need

obs = need("app.observability", "init", "trace_id_for", "mask", "span", "shutdown")

BLOB = base64.b64encode(os.urandom(3000)).decode()


@pytest.fixture(autouse=True)
def no_langfuse_env(monkeypatch):
    # Empty real env vars beat any Langfuse keys in the developer's local .env file.
    for var in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"):
        monkeypatch.setenv(var, "")
    monkeypatch.setenv("LANGFUSE_ENABLED", "false")


# ---- masking: no image data may ever leave the machine in a trace ----------------------------------------------

def test_data_urls_are_replaced():
    out = obs.mask({"image_url": "data:image/jpeg;base64," + BLOB})
    assert "data:image" not in str(out) and BLOB[:100] not in str(out)


def test_long_base64_blobs_are_replaced_even_without_a_data_prefix():
    assert BLOB[:100] not in str(obs.mask({"x": BLOB}))


def test_masking_recurses_through_lists_and_nested_models():
    payload = {"input": [{"content": [{"type": "input_image", "image_url": "data:image/png;base64," + BLOB}]}], "meta": ({"crop": BLOB},)}
    assert "data:image" not in str(obs.mask(payload)) and BLOB[:100] not in str(obs.mask(payload))


def test_normal_text_and_numbers_pass_through_unchanged():
    value = {"text": "Box 3 is crushed", "n": 4, "ok": True, "none": None, "ids": ["dmg_box_1"], "hash": "0123456789abcdef"}
    assert obs.mask(value) == value


def test_short_base64_like_ids_are_not_mangled():
    assert obs.mask("SGVsbG8=") == "SGVsbG8="


def test_masking_does_not_mutate_its_input():
    payload = {"image_url": "data:image/png;base64," + BLOB}
    before = dict(payload)
    obs.mask(payload)
    assert payload == before


def test_bytes_values_are_never_passed_through():
    assert b"\x89PNG" not in repr(obs.mask({"crop": b"\x89PNG" + os.urandom(2000)})).encode()


# ---- trace ids ------------------------------------------------------------------------------------------------

def test_trace_id_is_deterministic_and_well_formed():
    assert obs.trace_id_for("0123456789abcdef") == obs.trace_id_for("0123456789abcdef")
    assert re.fullmatch(r"[0-9a-f]{32}", obs.trace_id_for("0123456789abcdef"))


def test_trace_ids_differ_between_scans():
    assert obs.trace_id_for("0123456789abcdef") != obs.trace_id_for("fedcba9876543210")


# ---- failure drills: nothing here may change application behaviour ---------------------------------------------

def test_no_keys_means_disabled_noop():
    assert obs.init() is False
    with obs.span("anything", a=1):
        pass
    obs.shutdown()


@pytest.mark.parametrize("env", [{"LANGFUSE_ENABLED": "true"}, {"LANGFUSE_ENABLED": "true", "LANGFUSE_PUBLIC_KEY": "pk"}, {"LANGFUSE_PUBLIC_KEY": "pk", "LANGFUSE_SECRET_KEY": "sk"}])
def test_enabled_without_both_keys_or_keys_without_the_flag_stays_off(monkeypatch, env):
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    assert obs.init() is False


def test_wrong_keys_or_unreachable_service_never_raise(monkeypatch):
    monkeypatch.setenv("LANGFUSE_ENABLED", "true")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-wrong")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-wrong")
    monkeypatch.setenv("LANGFUSE_BASE_URL", "http://127.0.0.1:9")  # nothing listens here
    obs.init()
    with obs.span("scan", scan_id="x"):
        pass
    obs.shutdown()


def test_langfuse_not_installed_is_survivable(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def deny(name, *a, **k):
        if name.startswith("langfuse"):
            raise ImportError(name)
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", deny)
    monkeypatch.setenv("LANGFUSE_ENABLED", "true")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk")
    assert obs.init() is False


def test_errors_inside_the_traced_block_are_not_swallowed():
    with pytest.raises(ZeroDivisionError):
        with obs.span("work"):
            1 / 0


def test_a_broken_tracer_never_fails_the_traced_work(monkeypatch):
    client = type("Boom", (), {"__getattr__": lambda self, name: (_ for _ in ()).throw(RuntimeError("tracer down"))})()
    if hasattr(obs, "_client"):
        monkeypatch.setattr(obs, "_client", client)
    done = []
    with obs.span("work"):
        done.append(1)
    assert done == [1]


def test_scores_land_locally_even_when_tracing_is_down(tmp_path, monkeypatch):
    scoring = need("app.scoring", "record_scores", "SCORES_PATH")
    monkeypatch.setattr(scoring, "SCORES_PATH", tmp_path / "scores.jsonl")
    scoring.record_scores("0123456789abcdef", {"tool_call_validity": 1.0})
    assert (tmp_path / "scores.jsonl").read_text().strip()
