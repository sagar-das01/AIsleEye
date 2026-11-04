"""Phase 0: verify every Meta Model API capability AIsleEye depends on.

Usage (from backend/):
    python -m scripts.smoke_test [--image path/to/warehouse.jpg]
    python -m scripts.smoke_test --latency      # default vs each reasoning effort
"""

import argparse
import base64
import io
import json
import re
import sys
import time
from collections.abc import Callable
from pathlib import Path

from PIL import Image, ImageDraw

from app.clients import file_to_data_url, get_chat_model, get_client, reasoning_kwargs
from app.config import DATA_DIR, get_settings

OUT_DIR = DATA_DIR / "smoke"
SAM_PROMPTS = ["cardboard box", get_settings().walkway_concept]
LATENCY_EFFORTS = [None, "minimal", "low", "medium", "high"]


class Skip(Exception):
    """A check that cannot run in this environment (optional integration not configured)."""

BOX_RE = re.compile(r"<\|box;(x1=\d+;y1=\d+;x2=\d+;y2=\d+;w=\d+;h=\d+(?:;c=[\d.]+)?)\|>")
# Payload is base85 and may contain "|>", so only the header is read here.
MASK_HEADER_RE = re.compile(r"<\|mask;x=0;y=0(?:;c=[\d.]+)?;data=(\d+),(\d+),(.)")

DAMAGE_SCHEMA = {
    "type": "object",
    "properties": {
        "box_count": {"type": "integer"},
        "any_damage": {"type": "boolean"},
        "damage_types": {
            "type": "array",
            "items": {"type": "string", "enum": ["tear", "crushed_corner", "dent", "water_damage", "open_flap", "none"]},
        },
        "confidence": {"type": "number"},
        "reason": {"type": "string"},
    },
    "required": ["box_count", "any_damage", "damage_types", "confidence", "reason"],
    "additionalProperties": False,
}

ADJUST_INVENTORY_TOOL = {
    "type": "function",
    "name": "adjust_inventory",
    "description": "Move units of a SKU between stock states, e.g. to quarantine when damaged.",
    "parameters": {
        "type": "object",
        "properties": {
            "sku": {"type": "string"},
            "delta": {"type": "integer", "description": "Units to move into quarantine (positive)."},
            "reason": {"type": "string"},
        },
        "required": ["sku", "delta", "reason"],
        "additionalProperties": False,
    },
}


def make_synthetic_scene(path: Path) -> None:
    img = Image.new("RGB", (960, 640), (128, 128, 124))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 470, 960, 500], fill=(235, 200, 20))
    for x0, y0 in [(80, 220), (260, 220), (80, 60), (620, 380)]:
        d.rectangle([x0, y0, x0 + 170, y0 + 150], fill=(176, 128, 74), outline=(90, 60, 30), width=4)
        d.line([x0, y0 + 75, x0 + 170, y0 + 75], fill=(150, 105, 60), width=6)
    d.polygon([(260, 220), (310, 220), (260, 270)], fill=(128, 128, 124))
    img.save(path)


def _image_size(image_url: str) -> tuple[int, int] | None:
    try:
        return Image.open(io.BytesIO(base64.b64decode(image_url.split(",", 1)[1]))).size
    except Exception:
        return None


def _vision_input(prompt: str, image_url: str) -> list[dict]:
    return [{
        "type": "message",
        "role": "user",
        "content": [
            {"type": "input_text", "text": prompt},
            {"type": "input_image", "image_url": image_url},
        ],
    }]


def check_text(model: str, _: str) -> str:
    r = get_client().responses.create(model=model, input="Reply with exactly the word: OK", **reasoning_kwargs())
    assert r.output_text.strip(), "empty output"
    return r.output_text.strip()[:80]


def check_vision(model: str, image_url: str) -> str:
    r = get_client().responses.create(
        model=model, input=_vision_input("Describe this scene in one sentence.", image_url), **reasoning_kwargs()
    )
    assert r.output_text.strip(), "empty output"
    return r.output_text.strip()[:160]


def check_structured(model: str, image_url: str) -> str:
    r = get_client().responses.create(
        model=model,
        input=_vision_input("Inspect the cardboard boxes in this image for physical damage.", image_url),
        text={"format": {"type": "json_schema", "name": "BoxInspection", "schema": DAMAGE_SCHEMA, "strict": True}},
        **reasoning_kwargs(),
    )
    parsed = json.loads(r.output_text)
    missing = set(DAMAGE_SCHEMA["required"]) - parsed.keys()
    assert not missing, f"missing keys: {missing}"
    (OUT_DIR / "structured_output.json").write_text(json.dumps(parsed, indent=2))
    return json.dumps(parsed)[:200]


def check_tools(model: str, _: str) -> str:
    r = get_client().responses.create(
        model=model,
        input="Box SKU-1042 on pallet A3 has a crushed corner. Quarantine 1 unit.",
        tools=[ADJUST_INVENTORY_TOOL],
        **reasoning_kwargs(),
    )
    calls = [item for item in r.output if item.type == "function_call"]
    assert calls, f"no function_call; model said: {r.output_text[:120]!r}"
    return f"{calls[0].name}({calls[0].arguments})"


def check_sam(model: str, image_url: str) -> str:
    size = _image_size(image_url)
    summaries = []
    for prompt in SAM_PROMPTS:
        r = get_client().responses.create(
            model=model,
            input=_vision_input(prompt, image_url),
            metadata={"mask_encoding": "one_bit", "include_confidence": "true"},
        )
        raw = r.output_text or ""
        (OUT_DIR / f"sam_raw_{prompt.replace(' ', '_')}.txt").write_text(raw)
        boxes = BOX_RE.findall(raw)
        masks = MASK_HEADER_RE.findall(raw)
        frames = {(int(w), int(h)) for w, h in re.findall(r";w=(\d+);h=(\d+)", raw)}
        # Box coordinates are only usable as image pixels if SAM reports the frame we sent.
        if size and frames - {size}:
            raise AssertionError(f"SAM frame {sorted(frames)} differs from the sent image {size}: scale coordinates")
        first = f" first_box={boxes[0]} first_mask(h,w,enc)={masks[0]}" if boxes and masks else ""
        summaries.append(f"'{prompt}': {len(boxes)} boxes / {len(masks)} masks{first}")
    return " | ".join(summaries)


def check_langchain_agent(model: str, _: str) -> str:
    from langchain.agents import create_agent
    from langchain_core.tools import tool

    calls: list[tuple[str, int]] = []

    @tool
    def adjust_inventory(sku: str, delta: int, reason: str) -> str:
        """Move delta units of a SKU into quarantine."""
        calls.append((sku, delta))
        return f"moved {delta} of {sku}"

    agent = create_agent(get_chat_model(model), tools=[adjust_inventory], system_prompt="You are a warehouse agent. Use tools to act.")
    out = agent.invoke(
        {"messages": [{"role": "user", "content": "Box SKU-1042 has a crushed corner. Quarantine 1 unit."}]},
        config={"recursion_limit": 8},
    )
    assert calls, "agent finished without calling the tool"
    final = out["messages"][-1]
    assert isinstance(final.content, str) and final.content.strip(), "no final text answer"
    return f"tool call {calls[0]} then answer: {final.content.strip()[:80]}"


def check_langfuse(model: str, _: str) -> str:
    s = get_settings()
    if not (s.langfuse_enabled and s.langfuse_public_key and s.langfuse_secret_key.get_secret_value()):
        raise Skip("set LANGFUSE_ENABLED=true, LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY to test tracing")
    # Langfuse's HTTP and OpenTelemetry exporters ignore our per-client TLS context.
    import truststore
    from langfuse import Langfuse
    from langfuse.langchain import CallbackHandler

    truststore.inject_into_ssl()
    client = Langfuse(
        public_key=s.langfuse_public_key, secret_key=s.langfuse_secret_key.get_secret_value(), base_url=s.langfuse_base_url
    )
    assert client.auth_check(), "Langfuse rejected the keys"
    get_chat_model(model).invoke("Reply with exactly the word: OK", config={"callbacks": [CallbackHandler()]})
    client.flush()
    return f"keys accepted by {s.langfuse_base_url}; one LangChain trace sent (no image data)"


Checks = list[tuple[str, Callable[[str, str], str], str]]


def build_checks() -> Checks:
    s = get_settings()
    return [
        ("1. Muse Spark text", check_text, s.muse_model),
        ("2. Muse Spark vision", check_vision, s.muse_model),
        ("3. Structured output", check_structured, s.muse_model),
        ("4. Tool calling", check_tools, s.muse_model),
        ("5. SAM 3.1 segmentation", check_sam, s.sam_model),
        ("6. LangChain agent", check_langchain_agent, s.muse_model),
        ("7. Langfuse tracing (optional)", check_langfuse, s.muse_model),
    ]


def measure_latency(image_url: str, efforts=LATENCY_EFFORTS) -> list[dict]:
    """Time one vision call and one structured-output call per reasoning effort (None = model default)."""
    model = get_settings().muse_model
    rows = []
    for effort in efforts:
        extra = {"reasoning": {"effort": effort}} if effort else {}
        for name, kwargs in (
            ("vision", {}),
            ("structured", {"text": {"format": {"type": "json_schema", "name": "BoxInspection", "schema": DAMAGE_SCHEMA, "strict": True}}}),
        ):
            prompt = "Describe this scene in one sentence." if name == "vision" else "Inspect the cardboard boxes for damage."
            start = time.perf_counter()
            r = get_client().responses.create(model=model, input=_vision_input(prompt, image_url), **extra, **kwargs)
            usage = getattr(r, "usage", None)
            rows.append({
                "call": name,
                "effort": effort or "default",
                "seconds": round(time.perf_counter() - start, 1),
                "output_tokens": getattr(usage, "output_tokens", None),
            })
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", type=Path, help="Warehouse photo (jpg/png). Defaults to a synthetic scene.")
    parser.add_argument("--latency", action="store_true", help="Record default vs per-effort latency instead of the checks.")
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    image_path = args.image
    if image_path is None:
        image_path = OUT_DIR / "synthetic_scene.png"
        make_synthetic_scene(image_path)
    image_url = file_to_data_url(image_path)

    s = get_settings()
    print(f"Base URL: {s.model_api_base_url}\nImage:    {image_path}\n")

    if args.latency:
        rows = measure_latency(image_url)
        (OUT_DIR / "latency.json").write_text(json.dumps(rows, indent=2))
        for row in rows:
            print(f"{row['call']:<11}{row['effort']:<9}{row['seconds']:>6}s  {row['output_tokens']} output tokens")
        return 0

    checks = build_checks()
    failures = skipped = 0
    report = []
    for name, fn, model in checks:
        start = time.perf_counter()
        try:
            detail = fn(model, image_url)
            status = "PASS"
        except Skip as exc:
            detail, status = str(exc), "SKIP"
            skipped += 1
        except Exception as exc:
            detail, status = f"{type(exc).__name__}: {exc}", "FAIL"
            failures += 1
        seconds = round(time.perf_counter() - start, 1)
        print(f"{status:<5} {name} [{model}] ({seconds}s)\n      {detail}")
        report.append({"check": name, "model": model, "status": status, "seconds": seconds})
    (OUT_DIR / "report.json").write_text(json.dumps(report, indent=2))
    print(f"\n{len(checks) - failures - skipped}/{len(checks) - skipped} passed ({skipped} skipped). Raw outputs in {OUT_DIR}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
