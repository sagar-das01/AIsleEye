"""Run every demo photo through the live pipeline and pin the results for offline replay.

Usage (from backend/):  python -m scripts.freeze_demo [name ...]
Needs the Meta Model API. Re-run it whenever preprocessing, privacy blur, the guard or the thresholds change.
"""

import asyncio
import sys
import time

from app import replay
from app.pipeline import scan


async def freeze(names: list[str]) -> None:
    replay.DEMO_DIR.mkdir(parents=True, exist_ok=True)
    entries = [e for e in replay.load_manifest() if not names or e["id"] in names]
    for entry in entries:
        data = replay.demo_image_path(entry["id"]).read_bytes()
        scan.CACHE_DIR = replay.DEMO_DIR.parent / "freeze-tmp"  # never read an older cached result
        start = time.perf_counter()
        result = await scan.run_scan(data, "image/jpeg")
        pinned = result.model_copy(update={"timings_ms": {}})  # timings of one run are not a property of the photo
        (replay.DEMO_DIR / f"{result.scan_id}.json").write_text(pinned.model_dump_json(indent=1))
        damaged = sum(f.type == "box_damage" for f in result.findings)
        blocked = sum(f.type == "walkway_blocked" for f in result.findings)
        want = entry["expected"]
        flag = "ok " if (damaged, blocked) == (want["damaged"], want["blocked"]) else "DIFF"
        print(f"{flag} {entry['id']:<16} damaged={damaged} (want {want['damaged']}) blocked={blocked} (want {want['blocked']}) {time.perf_counter() - start:.1f}s")
        if flag == "DIFF":
            for f in result.findings:
                print(f"     {f.id}: {f.severity} {f.confidence:.2f} {f.description[:100]}")


def main(names: list[str]) -> int:
    asyncio.run(freeze(names))  # one loop for every photo: the cached async client is bound to it
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
