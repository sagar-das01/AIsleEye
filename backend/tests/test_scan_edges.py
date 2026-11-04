import base64
import io
import json
import re
from concurrent.futures import ThreadPoolExecutor

import httpx
import openai
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from spec_support import need

main = need("app.main", "app")
scan_mod = need("app.pipeline.scan", "load_image", "run_scan", "CACHE_DIR")
from app.pipeline import preprocess  # noqa: E402
fakes = need("fakes", "install_fake_model", "sam_record", "sam_line", "concept_of")

W, H = 100, 80
client = TestClient(main.app)
safe_client = TestClient(main.app, raise_server_exceptions=False)


def image_bytes(fmt="PNG", mode="RGB", size=(W, H), color=None) -> bytes:
    buf = io.BytesIO()
    Image.new(mode, size, color if color is not None else 0).save(buf, format=fmt)
    return buf.getvalue()


def sent_size(kwargs) -> tuple[int, int]:
    content = kwargs["input"][0]["content"]
    url = next(c["image_url"] for c in content if c["type"] == "input_image")
    return Image.open(io.BytesIO(base64.b64decode(url.split(",", 1)[1]))).size


def reply(damaged=True, severity="low"):
    return json.dumps({"assessments": [{"index": 0, "damaged": damaged, "damage_type": "dent" if damaged else "none",
                                        "severity": severity, "confidence": 0.7, "reason": "Side dent"}]})


def good_handler(kw):
    if kw["model"] == "sam-3.1":
        w, h = sent_size(kw)  # answer in the coordinates of the image we were actually sent
        if fakes.concept_of(kw) == "cardboard box":
            return fakes.sam_line(fakes.sam_record(0, w // 5, h // 3, w // 2, h * 3 // 4, w=w, h=h, c=0.95))
        return fakes.sam_line(fakes.sam_record(0, 0, h * 5 // 8, w - 1, h * 3 // 4, w=w, h=h, c=0.8))
    return reply()


@pytest.fixture(autouse=True)
def cache_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(scan_mod, "CACHE_DIR", tmp_path / "cache")
    return tmp_path / "cache"


@pytest.fixture
def fake(monkeypatch):
    return fakes.install_fake_model(monkeypatch, good_handler)


def post(data: bytes, mime="image/png", *, field="image", name="photo", c=client):
    return c.post("/scan", files={field: (name, data, mime)})


# ---- upload validation ----------------------------------------------------------------------------------------

@pytest.mark.parametrize("mime", ["image/gif", "image/svg+xml", "application/pdf", "text/plain", "application/octet-stream", ""])
def test_unsupported_content_types_are_415(fake, mime):
    assert post(image_bytes(), mime).status_code == 415
    assert fake.calls == []


def test_missing_file_field_is_422(fake):
    assert client.post("/scan").status_code == 422
    assert post(image_bytes(), field="file").status_code == 422
    assert fake.calls == []


def test_empty_file_is_rejected_cleanly(fake):
    assert post(b"").status_code == 422
    assert fake.calls == []


def test_truncated_image_is_422_not_500(fake):
    data = image_bytes("PNG", size=(300, 300), color=(10, 200, 30))
    assert post(data[: len(data) // 2], c=safe_client).status_code == 422
    assert fake.calls == []


def test_text_pretending_to_be_an_image_is_422(fake):
    assert post(b"<script>alert(1)</script>", "image/png").status_code == 422


def test_html_polyglot_with_valid_png_header_is_still_decoded_safely(fake):
    data = image_bytes() + b"<script>alert(1)</script>"
    res = post(data)
    assert res.status_code in (200, 422)
    assert "<script>" not in res.text


@pytest.mark.parametrize(("fmt", "mime"), [("PNG", "image/png"), ("JPEG", "image/jpeg"), ("WEBP", "image/webp")])
def test_each_supported_format_is_accepted(fake, fmt, mime):
    assert post(image_bytes(fmt), mime).status_code == 200


@pytest.mark.parametrize(("fmt", "mime"), [("PNG", "image/jpeg"), ("JPEG", "image/webp"), ("WEBP", "image/png")])
def test_declared_type_must_match_the_content(fake, fmt, mime):
    assert post(image_bytes(fmt), mime).status_code == 422
    assert fake.calls == []


@pytest.mark.parametrize(("fmt", "mode"), [("PNG", "RGBA"), ("PNG", "L"), ("PNG", "P"), ("JPEG", "L"), ("JPEG", "CMYK"), ("WEBP", "RGBA")])
def test_unusual_pixel_modes_are_normalised_to_rgb(fake, fmt, mode):
    img = scan_mod.load_image(image_bytes(fmt, mode), f"image/{fmt.lower()}")
    assert img.mode == "RGB"
    assert post(image_bytes(fmt, mode), f"image/{fmt.lower()}").status_code == 200


def test_upload_size_boundary_is_inclusive(fake, monkeypatch):
    data = image_bytes()
    monkeypatch.setattr(main, "MAX_UPLOAD_BYTES", len(data))
    assert post(data).status_code == 200
    monkeypatch.setattr(main, "MAX_UPLOAD_BYTES", len(data) - 1)
    assert post(data).status_code == 413


def test_default_upload_limit_is_ten_megabytes():
    assert main.MAX_UPLOAD_BYTES == 10 * 1024 * 1024


def test_pixel_bomb_is_rejected_before_any_model_call(fake, monkeypatch):
    monkeypatch.setattr(preprocess, "MAX_PIXELS", W * H - 1)
    res = post(image_bytes())
    assert res.status_code == 422 and "too large" in res.json()["detail"].lower()
    assert fake.calls == []


def test_pillow_decompression_bomb_guard_is_mapped_to_422(fake, monkeypatch):
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 10)
    assert post(image_bytes(), c=safe_client).status_code == 422
    assert fake.calls == []


def test_exact_pixel_limit_is_allowed(fake, monkeypatch):
    monkeypatch.setattr(preprocess, "MAX_PIXELS", W * H)
    assert post(image_bytes()).status_code == 200


def test_client_supplied_filename_is_never_used_for_storage(fake, tmp_path, cache_dir):
    assert post(image_bytes(), name="../../evil.png").status_code == 200
    assert not list(tmp_path.glob("**/evil*"))
    assert [p.suffix for p in cache_dir.iterdir()] == [".json"]


# ---- processing -----------------------------------------------------------------------------------------------

def test_downscaled_photo_keeps_every_coordinate_inside_the_reported_size(fake, monkeypatch):
    monkeypatch.setattr(preprocess, "MAX_SIDE", 50)
    body = post(image_bytes(size=(200, 100))).json()
    w, h = body["image_size"]["width"], body["image_size"]["height"]
    assert (w, h) == (50, 25)
    assert all(sent_size(c) == (50, 25) for c in fake.calls if c["model"] == "sam-3.1")
    for box in [o["bbox"] for o in body["objects"]] + [f["bbox"] for f in body["findings"]]:
        assert 0 <= box["x1"] < box["x2"] <= w and 0 <= box["y1"] < box["y2"] <= h
    for obj in body["objects"]:
        nums = [float(n) for n in re.findall(r"-?\d+(?:\.\d+)?", obj["mask_svg"])]
        assert all(-1 <= n <= max(w, h) + 1 for n in nums)


def test_image_exactly_at_the_side_limit_is_not_resized(fake, monkeypatch):
    monkeypatch.setattr(preprocess, "MAX_SIDE", 100)
    assert post(image_bytes(size=(100, 60))).json()["image_size"] == {"width": 100, "height": 60}


def test_portrait_and_landscape_both_downscale_proportionally(monkeypatch):
    monkeypatch.setattr(preprocess, "MAX_SIDE", 40)
    assert scan_mod.load_image(image_bytes(size=(80, 160)), "image/png").size == (20, 40)
    assert scan_mod.load_image(image_bytes(size=(160, 80)), "image/png").size == (40, 20)


def test_no_boxes_found_is_a_valid_empty_result(monkeypatch, cache_dir):
    fakes.install_fake_model(monkeypatch, lambda kw: "")
    res = post(image_bytes())
    assert res.status_code == 200
    body = res.json()
    assert body["objects"] == [] and body["findings"] == []
    assert (cache_dir / f"{body['scan_id']}.json").is_file()


def test_boxes_without_walkway_only_report_damage(monkeypatch):
    fakes.install_fake_model(monkeypatch, lambda kw: "" if fakes.concept_of(kw) == "yellow line" else good_handler(kw))
    body = post(image_bytes()).json()
    assert [f["type"] for f in body["findings"]] == ["box_damage"]


def test_walkway_without_boxes_reports_nothing(monkeypatch):
    fakes.install_fake_model(monkeypatch, lambda kw: "" if fakes.concept_of(kw) == "cardboard box" else good_handler(kw))
    assert post(image_bytes()).json()["findings"] == []


def test_unusable_inspection_output_still_returns_geometry_findings(monkeypatch):
    fakes.install_fake_model(monkeypatch, lambda kw: "I am sorry, I cannot help." if kw["model"] != "sam-3.1" else good_handler(kw))
    body = post(image_bytes()).json()
    assert [f["type"] for f in body["findings"]] == ["walkway_blocked"]


def test_intact_boxes_produce_no_damage_finding(monkeypatch):
    fakes.install_fake_model(monkeypatch, lambda kw: reply(damaged=False) if kw["model"] != "sam-3.1" else good_handler(kw))
    assert "box_damage" not in {f["type"] for f in post(image_bytes()).json()["findings"]}


def test_many_boxes_are_inspected_in_chunks_and_all_reported(monkeypatch):
    def handler(kw):
        if kw["model"] == "sam-3.1":
            if fakes.concept_of(kw) == "yellow line":
                return ""
            recs = [fakes.sam_record(i, (i % 12) * 8, (i // 12) * 8, (i % 12) * 8 + 5, (i // 12) * 8 + 5, w=100, h=80) for i in range(60)]
            return fakes.sam_line(*recs)
        n = sum(1 for c in kw["input"][0]["content"] if c["type"] == "input_image")
        return json.dumps({"assessments": [{"index": i, "damaged": True, "damage_type": "tear", "severity": "low",
                                            "confidence": 0.6, "reason": "x"} for i in range(n)]})

    fake = fakes.install_fake_model(monkeypatch, handler)
    body = post(image_bytes(size=(100, 80))).json()
    assert len([f for f in body["findings"] if f["type"] == "box_damage"]) == 60
    assert len([c for c in fake.calls if c["model"] != "sam-3.1"]) == 2


def test_result_invariants(fake):
    body = post(image_bytes()).json()
    ids = [f["id"] for f in body["findings"]]
    assert len(ids) == len(set(ids))
    assert all(0.0 <= f["confidence"] <= 1.0 for f in body["findings"] + body["objects"])
    assert all(isinstance(v, int) and v >= 0 for v in body["timings_ms"].values())
    assert re.fullmatch(r"[0-9a-f]{16}", body["scan_id"])


def test_response_never_contains_image_data(fake):
    text = post(image_bytes()).text
    assert "data:image" not in text and "base64" not in text


# ---- model failure --------------------------------------------------------------------------------------------

def request():
    return httpx.Request("POST", "https://api.meta.ai/v1/responses")


@pytest.mark.parametrize("which", ["sam-3.1", "muse-spark-1.3"])
def test_a_model_timeout_is_a_504_and_nothing_is_cached(monkeypatch, cache_dir, which):
    err = openai.APITimeoutError(request=request())
    fakes.install_fake_model(monkeypatch, lambda kw: err if kw["model"] == which else good_handler(kw))
    res = post(image_bytes())
    assert res.status_code == 504
    assert not cache_dir.exists() or list(cache_dir.iterdir()) == []


@pytest.mark.parametrize("which", ["sam-3.1", "muse-spark-1.3"])
def test_failure_of_either_model_is_a_502_and_nothing_is_cached(monkeypatch, cache_dir, which):
    err = openai.InternalServerError("boom", response=httpx.Response(500, request=request()), body=None)
    fakes.install_fake_model(monkeypatch, lambda kw: err if kw["model"] == which else good_handler(kw))
    res = post(image_bytes())
    assert res.status_code == 502
    assert not cache_dir.exists() or list(cache_dir.iterdir()) == []


@pytest.mark.parametrize(
    "error",
    [
        openai.RateLimitError("limit sk-secret", response=httpx.Response(429, request=request()), body=None),
        openai.AuthenticationError("bad key sk-secret", response=httpx.Response(401, request=request()), body=None),
        openai.InternalServerError("boom sk-secret", response=httpx.Response(500, request=request()), body=None),
    ],
    ids=["429", "401", "500"],
)
def test_upstream_errors_are_502_and_never_leak_details(monkeypatch, error):
    fakes.install_fake_model(monkeypatch, lambda kw: error)
    res = post(image_bytes())
    assert res.status_code == 502
    assert "sk-secret" not in res.text and "test-key" not in res.text


def test_unexpected_errors_are_500_without_a_traceback(monkeypatch):
    fakes.install_fake_model(monkeypatch, lambda kw: RuntimeError("secret internals /etc/passwd"))
    res = post(image_bytes(), c=safe_client)
    assert res.status_code == 500
    assert "secret internals" not in res.text and "Traceback" not in res.text


def test_service_recovers_after_a_failed_scan(monkeypatch):
    calls = {"n": 0}

    def flaky(kw):
        calls["n"] += 1
        return openai.APIConnectionError(request=request()) if calls["n"] <= 2 else good_handler(kw)

    fakes.install_fake_model(monkeypatch, flaky)
    data = image_bytes()
    assert post(data).status_code == 502
    assert post(data).status_code == 200


# ---- cache ----------------------------------------------------------------------------------------------------

def test_cache_dir_is_created_on_demand_and_holds_only_final_files(fake, cache_dir):
    assert not cache_dir.exists()
    post(image_bytes())
    assert [p.suffix for p in cache_dir.iterdir()] == [".json"]


def test_cached_file_is_valid_schema_json(fake, cache_dir):
    body = post(image_bytes()).json()
    from app.schemas import ScanResult

    assert ScanResult.model_validate_json((cache_dir / f"{body['scan_id']}.json").read_text()).scan_id == body["scan_id"]


def test_reencoded_copy_of_the_same_picture_is_a_different_scan(fake):
    a = post(image_bytes("PNG")).json()["scan_id"]
    b = post(image_bytes("JPEG"), "image/jpeg").json()["scan_id"]
    assert a != b


def test_cache_hit_skips_models_but_still_validates_the_upload(fake):
    data = image_bytes()
    post(data)
    n = len(fake.calls)
    assert post(data[:10]).status_code == 422
    assert post(data).status_code == 200 and len(fake.calls) == n


def test_concurrent_identical_uploads_leave_one_valid_cache_file(fake, cache_dir):
    data = image_bytes()
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda _: post(data), range(6)))
    assert {r.status_code for r in results} == {200}
    assert len({r.json()["scan_id"] for r in results}) == 1
    files = list(cache_dir.iterdir())
    assert len(files) == 1 and json.loads(files[0].read_text())["scan_id"] == results[0].json()["scan_id"]


def test_save_failure_leaves_no_partial_cache_entry(fake, cache_dir, monkeypatch):
    import os

    monkeypatch.setattr(os, "replace", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    assert post(image_bytes(), c=safe_client).status_code == 500
    assert not list(cache_dir.glob("*.json"))


def corrupt(cache_dir, scan_id, payload):
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / f"{scan_id}.json").write_text(payload)


@pytest.mark.parametrize("payload", ["", "{not json", "{}", '{"scan_id": "x"}', "null"])
def test_corrupt_cache_entry_is_treated_as_a_miss(fake, cache_dir, payload):
    data = image_bytes()
    corrupt(cache_dir, scan_mod.scan_id_for(data), payload)
    assert post(data, c=safe_client).status_code == 200


def test_get_scan_on_a_corrupt_entry_is_404_not_500(cache_dir):
    corrupt(cache_dir, "0123456789abcdef", "{not json")
    assert safe_client.get("/scan/0123456789abcdef").status_code == 404


# ---- GET /scan/{id} -------------------------------------------------------------------------------------------

@pytest.mark.parametrize(
    "bad",
    ["0123456789abcde", "0123456789abcdef0", "0123456789ABCDEF", "0123456789abcdeg", "０１２３４５６７８９ａｂｃｄｅｆ", "%20" * 16, "-" * 16],
)
def test_scan_ids_must_be_exactly_sixteen_lowercase_hex_chars(bad):
    assert client.get(f"/scan/{bad}").status_code == 422


def test_scan_id_trailing_newline_is_not_accepted():
    assert client.get("/scan/0123456789abcdef%0A").status_code == 422


def test_get_scan_returns_exactly_what_post_returned(fake):
    first = post(image_bytes()).json()
    assert client.get(f"/scan/{first['scan_id']}").json() == first


def test_scan_listing_is_not_exposed():
    assert client.get("/scan").status_code in (404, 405)
    assert client.get("/scan/").status_code in (307, 404, 405)


def test_get_scan_is_read_only():
    for method in ("post", "put", "delete"):
        assert getattr(client, method)("/scan/0123456789abcdef").status_code in (404, 405)


def test_weak_walkway_matches_are_dropped_before_they_can_cause_hazards(monkeypatch):
    def handler(kw):
        if kw["model"] == "sam-3.1":
            w, h = sent_size(kw)
            if fakes.concept_of(kw) == "cardboard box":
                return fakes.sam_line(fakes.sam_record(0, w // 5, h // 3, w // 2, h * 3 // 4, w=w, h=h, c=0.95))
            return fakes.sam_line(fakes.sam_record(0, 0, h * 5 // 8, w - 1, h * 3 // 4, w=w, h=h, c=0.45))
        return reply(damaged=False)

    fakes.install_fake_model(monkeypatch, handler)
    body = post(image_bytes()).json()
    assert [o["label"] for o in body["objects"]] == ["box"]
    assert body["findings"] == []


@pytest.mark.parametrize(("confidence", "kept"), [(0.59, False), (0.6, True), (0.9, True)])
def test_walkway_confidence_floor_boundary(monkeypatch, confidence, kept):
    def handler(kw):
        if kw["model"] != "sam-3.1":
            return reply(damaged=False)
        w, h = sent_size(kw)
        if fakes.concept_of(kw) == "cardboard box":
            return fakes.sam_line(fakes.sam_record(0, w // 5, h // 3, w // 2, h * 3 // 4, w=w, h=h, c=0.95))
        return fakes.sam_line(fakes.sam_record(0, 0, h * 5 // 8, w - 1, h * 3 // 4, w=w, h=h, c=confidence))

    fakes.install_fake_model(monkeypatch, handler)
    labels = [o["label"] for o in post(image_bytes()).json()["objects"]]
    assert ("walkway" in labels) is kept
