"""Phase 0: verify every Meta Model API capability AIsleEye depends on.

Usage (from backend/):  python -m scripts.smoke_test [--image path/to/warehouse.jpg]
"""

import argparse
import json
import re
import sys
import time
from collections.abc import Callable
from pathlib import Path

from PIL import Image, ImageDraw

from app.clients import file_to_data_url, get_client
from app.config import DATA_DIR, get_settings

OUT_DIR = DATA_DIR / "smoke"
SAM_PROMPTS = ["cardboard box", "floor tape"]

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


def check_text(model: str, _: str) -> str:
    r = get_client().responses.create(model=model, input="Reply with exactly the word: OK")
    assert r.output_text.strip(), "empty output"
    return r.output_text.strip()[:80]


def check_vision(model: str, image_url: str) -> str:
    r = get_client().responses.create(
        model=model,
        input=[{
            "type": "message",
            "role": "user",
            "content": [
                {"type": "input_text", "text": "Describe this scene in one sentence."},
                {"type": "input_image", "image_url": image_url},
            ],
        }],
    )
    assert r.output_text.strip(), "empty output"
    return r.output_text.strip()[:160]


def check_structured(model: str, image_url: str) -> str:
    r = get_client().responses.create(
        model=model,
        input=[{
            "type": "message",
            "role": "user",
            "content": [
                {"type": "input_text", "text": "Inspect the cardboard boxes in this image for physical damage."},
                {"type": "input_image", "image_url": image_url},
            ],
        }],
        text={"format": {"type": "json_schema", "name": "BoxInspection", "schema": DAMAGE_SCHEMA, "strict": True}},
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
    )
    calls = [item for item in r.output if item.type == "function_call"]
    assert calls, f"no function_call; model said: {r.output_text[:120]!r}"
    return f"{calls[0].name}({calls[0].arguments})"


def check_sam(model: str, image_url: str) -> str:
    summaries = []
    for prompt in SAM_PROMPTS:
        r = get_client().responses.create(
            model=model,
            input=[{
                "type": "message",
                "role": "user",
                "content": [
                    {"type": "input_text", "text": prompt},
                    {"type": "input_image", "image_url": image_url},
                ],
            }],
            metadata={"mask_encoding": "one_bit", "include_confidence": "true"},
        )
        raw = r.output_text or ""
        (OUT_DIR / f"sam_raw_{prompt.replace(' ', '_')}.txt").write_text(raw)
        boxes = BOX_RE.findall(raw)
        masks = MASK_HEADER_RE.findall(raw)
        first = f" first_box={boxes[0]} first_mask(h,w,enc)={masks[0]}" if boxes and masks else ""
        summaries.append(f"'{prompt}': {len(boxes)} boxes / {len(masks)} masks{first}")
    return " | ".join(summaries)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", type=Path, help="Warehouse photo (jpg/png). Defaults to a synthetic scene.")
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    image_path = args.image
    if image_path is None:
        image_path = OUT_DIR / "synthetic_scene.png"
        make_synthetic_scene(image_path)
    image_url = file_to_data_url(image_path)

    s = get_settings()
    checks: list[tuple[str, Callable[[str, str], str], str]] = [
        ("1. Muse Spark text", check_text, s.muse_model),
        ("2. Muse Spark vision", check_vision, s.muse_model),
        ("3. Structured output", check_structured, s.muse_model),
        ("4. Tool calling", check_tools, s.muse_model),
        ("5. SAM 3.1 segmentation", check_sam, s.sam_model),
    ]

    print(f"Base URL: {s.model_api_base_url}\nImage:    {image_path}\n")
    failures = 0
    for name, fn, model in checks:
        start = time.perf_counter()
        try:
            detail = fn(model, image_url)
            print(f"PASS  {name} [{model}] ({time.perf_counter() - start:.1f}s)\n      {detail}")
        except Exception as exc:
            failures += 1
            print(f"FAIL  {name} [{model}] ({time.perf_counter() - start:.1f}s)\n      {type(exc).__name__}: {exc}")
    print(f"\n{len(checks) - failures}/{len(checks)} passed. Raw outputs in {OUT_DIR}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
