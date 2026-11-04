"""Render the demo photos (synthetic, drawn here, so they carry no third-party licence).

Usage (from backend/):  python -m scripts.make_demo_images
Writes demo_images/*.jpg and demo_images/manifest.json at the repository root.
"""

import json
import random
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from app.config import ROOT_DIR

OUT = ROOT_DIR / "demo_images"
W, H = 1280, 800
WALKWAY = (468, 586)  # y range of the painted yellow lane

KRAFT = [(176, 132, 84), (168, 124, 78), (184, 140, 92), (160, 118, 74)]


@dataclass
class Box:
    x: int  # left of the front face
    base: int  # y of the bottom edge on the floor
    w: int = 170
    h: int = 140
    tone: int = 0
    damage: str | None = None  # crushed_corner | tear | water | open_flap


def floor(seed: int) -> Image.Image:
    rng = np.random.default_rng(seed)
    y = np.linspace(0, 1, H)[:, None]
    base = 96 + 44 * y  # darker far wall, lighter near floor
    img = np.tile(base[:, :, None], (1, W, 3)) * np.array([1.0, 1.0, 0.97])
    img += rng.normal(0, 4.5, (H, W, 1))
    image = Image.fromarray(np.clip(img, 0, 255).astype("uint8"))
    d = ImageDraw.Draw(image)
    d.rectangle([0, 0, W, 250], fill=(78, 82, 88))  # back wall
    for x in range(0, W, 160):  # racking uprights
        d.rectangle([x, 40, x + 14, 250], fill=(52, 90, 150))
    d.rectangle([0, 130, W, 142], fill=(206, 110, 40))
    d.rectangle([0, 238, W, 250], fill=(206, 110, 40))
    for x in range(-200, W + 200, 320):  # expansion joints
        d.line([x, 250, x - 160, H], fill=(84, 84, 82), width=2)
    return image.filter(ImageFilter.GaussianBlur(0.6))


def lane(image: Image.Image, seed: int) -> None:
    rng = np.random.default_rng(seed)
    d = ImageDraw.Draw(image)
    top, bottom = WALKWAY
    d.rectangle([0, top, W, bottom], fill=(232, 188, 24))
    d.rectangle([0, top, W, top + 5], fill=(190, 150, 20))
    d.rectangle([0, bottom - 5, W, bottom], fill=(190, 150, 20))
    arr = np.asarray(image).astype(np.int16)
    arr[top:bottom] += rng.normal(0, 6, (bottom - top, W, 1)).astype(np.int16)  # worn paint
    image.paste(Image.fromarray(np.clip(arr, 0, 255).astype("uint8")))


def draw_box(image: Image.Image, b: Box) -> None:
    d = ImageDraw.Draw(image, "RGBA")
    x, y, w, h = b.x, b.base - b.h, b.w, b.h
    dep = int(w * 0.28)
    rise = int(dep * 0.55)
    kraft = KRAFT[b.tone % len(KRAFT)]
    front = kraft
    top = tuple(min(255, c + 26) for c in kraft)
    side = tuple(max(0, c - 34) for c in kraft)
    d.ellipse([x - 10, b.base - 14, x + w + dep + 14, b.base + 18], fill=(20, 20, 20, 90))  # contact shadow
    d.polygon([(x + w, y), (x + w + dep, y - rise), (x + w + dep, y + h - rise), (x + w, y + h)], fill=side, outline=(96, 66, 36))
    d.polygon([(x, y), (x + dep, y - rise), (x + w + dep, y - rise), (x + w, y)], fill=top, outline=(96, 66, 36))
    d.rectangle([x, y, x + w, y + h], fill=front, outline=(96, 66, 36), width=2)
    mid = x + w // 2
    d.polygon([(mid - 14, y), (mid - 14 + dep, y - rise), (mid + 14 + dep, y - rise), (mid + 14, y)], fill=(222, 204, 160))  # tape on top
    d.rectangle([mid - 14, y, mid + 14, y + int(h * 0.42)], fill=(222, 204, 160))  # tape down the front
    d.rectangle([x + 14, y + h - 58, x + 14 + 70, y + h - 16], fill=(245, 245, 240), outline=(120, 120, 120))  # label
    for i in range(3):
        d.line([x + 20, y + h - 50 + i * 11, x + 78 - i * 8, y + h - 50 + i * 11], fill=(70, 70, 70), width=2)
    if b.damage:
        _damage(d, b, x, y, w, h, dep, rise)


def _damage(d: ImageDraw.ImageDraw, b: Box, x: int, y: int, w: int, h: int, dep: int, rise: int) -> None:
    rng = random.Random(x * 31 + b.base)
    if b.damage == "crushed_corner":
        pts = [(x - 2, y - 2), (x + 62, y - 2), (x + 38, y + 22), (x + 14, y + 38), (x - 2, y + 74)]
        d.polygon(pts, fill=(112, 112, 108))  # corner caved in, floor visible through it
        for k in range(5):
            d.line([(x + 20 + k * 9, y + 4 + k * 3), (x + 4 + k * 12, y + 34 + k * 8)], fill=(80, 56, 32), width=3)
        d.line([(x + 62, y), (x + 38, y + 22), (x + 14, y + 38), (x, y + 74)], fill=(70, 48, 28), width=4)
    elif b.damage == "tear":
        cx, top = x + int(w * 0.6), y + 14
        zig = [(cx + rng.randint(-10, 10) + (k % 2) * 14, top + k * 14) for k in range(9)]
        d.line(zig, fill=(52, 34, 20), width=11)
        d.line(zig, fill=(138, 104, 70), width=4)
        d.polygon([zig[0], (zig[0][0] - 26, zig[0][1] - 6), (zig[2][0] - 10, zig[2][1])], fill=(206, 164, 112))
    elif b.damage == "water":
        for _ in range(7):
            r = rng.randint(22, 46)
            cx, cy = x + rng.randint(10, w - 10), y + h - rng.randint(6, 44)
            d.ellipse([cx - r, cy - r // 2, cx + r, cy + r // 2], fill=(96, 70, 44, 150))
        d.rectangle([x, y + h - 22, x + w, y + h], fill=(84, 60, 38, 170))
        for k in range(0, w, 17):
            d.line([x + k, y + h - 24, x + k + rng.randint(-5, 5), y + h - 56], fill=(110, 82, 52, 150), width=3)
    elif b.damage == "open_flap":
        d.polygon([(x, y), (x - 34, y - 60), (x + w // 2 - 6, y - 52), (x + w // 2 - 6, y)], fill=(196, 152, 100), outline=(96, 66, 36))
        d.polygon([(x + w, y), (x + w + 36, y - 66), (x + w // 2 + 6, y - 54), (x + w // 2 + 6, y)], fill=(188, 144, 94), outline=(96, 66, 36))
        d.rectangle([x + w // 2 - 6, y - 4, x + w // 2 + 6, y + 6], fill=(60, 40, 24))


def scene(seed: int, boxes: list[Box], with_lane: bool = True) -> Image.Image:
    image = floor(seed)
    if with_lane:
        lane(image, seed)
    for b in sorted(boxes, key=lambda b: (b.base, b.x)):
        draw_box(image, b)
    return image.filter(ImageFilter.GaussianBlur(0.5))


def stack(x: int, base: int, w: int = 170, h: int = 140, rows: int = 2, damage: dict | None = None, tone: int = 0) -> list[Box]:
    damage = damage or {}
    return [Box(x=x, base=base - r * h, w=w, h=h, tone=tone + r, damage=damage.get(r)) for r in range(rows)]


SCENES = {
    "clean_pallet_a": (
        "Clean pallet", "Two tidy stacks well clear of the marked walkway.", {"damaged": 0, "blocked": 0}, None,
        lambda: scene(11, stack(90, 450, rows=2) + stack(330, 450, rows=2, tone=1) + stack(760, 770, 190, 150, rows=1, tone=2) + stack(1010, 770, 170, 140, rows=1, tone=3)),
    ),
    "clean_pallet_b": (
        "Clean aisle", "Boxes in a row along the back, walkway clear.", {"damaged": 0, "blocked": 0}, None,
        lambda: scene(12, stack(70, 440, 150, 120, 2) + stack(260, 440, 150, 120, 2, tone=1) + stack(450, 440, 150, 120, 1, tone=2) + stack(900, 440, 170, 130, 2, tone=3)),
    ),
    "damaged_crushed": (
        "Crushed corner", "One box with a caved-in top corner on an otherwise tidy stack.", {"damaged": 1, "blocked": 0}, "SKU-1042",
        lambda: scene(13, stack(110, 450, rows=2, damage={1: "crushed_corner"}) + stack(380, 450, rows=2, tone=1) + stack(860, 780, 190, 150, 1, tone=2)),
    ),
    "damaged_wet": (
        "Water damage and a tear", "A water-stained box and one with a torn side.", {"damaged": 2, "blocked": 0}, "SKU-2003",
        lambda: scene(14, stack(90, 450, rows=1, damage={0: "water"}, tone=0) + stack(330, 450, rows=2, damage={0: "tear"}, tone=1) + stack(780, 780, 180, 150, 1, tone=2)),
    ),
    "blocked_aisle": (
        "Blocked walkway", "A box standing in the yellow pedestrian lane.", {"damaged": 0, "blocked": 1}, None,
        lambda: scene(15, [Box(x=520, base=600, w=190, h=150, tone=1)] + stack(90, 440, rows=2) + stack(900, 440, rows=2, tone=2)),
    ),
    "blocked_two": (
        "Two boxes in the lane", "A stack and a single box both sitting on the walkway.", {"damaged": 0, "blocked": 2}, None,
        lambda: scene(16, stack(250, 604, 170, 140, 1, tone=0) + stack(640, 600, 180, 150, 2, tone=1) + stack(1000, 440, rows=2, tone=2)),
    ),
    "hero_mixed": (
        "Mixed scene", "A damaged box, a box blocking the walkway and tidy stock.", {"damaged": 1, "blocked": 1}, "SKU-3310",
        lambda: scene(17, stack(90, 450, rows=2, damage={1: "open_flap"}) + stack(360, 450, rows=2, tone=1) + [Box(x=640, base=604, w=200, h=160, tone=2)] + stack(950, 780, 190, 150, 1, tone=3)),
    ),
}


def main() -> None:
    OUT.mkdir(exist_ok=True)
    manifest = []
    for name, (title, description, expected, sku, render) in SCENES.items():
        render().save(OUT / f"{name}.jpg", format="JPEG", quality=90, optimize=True)
        manifest.append({"id": name, "title": title, "description": description, "expected": expected, "sku": sku})
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"Wrote {len(manifest)} demo photos to {OUT}")


if __name__ == "__main__":
    main()
