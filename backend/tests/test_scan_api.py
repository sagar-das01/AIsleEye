import base64
import hashlib
import io
import json

import httpx
import openai
import pytest
from fakes import concept_of, install_fake_model, sam_line, sam_record
from fastapi.testclient import TestClient
from PIL import Image

from app import main
from app.pipeline import scan as scan_mod

W, H = 100, 80
client = TestClient(main.app)


def image_bytes(fmt="PNG", color=(120, 120, 120), exif=None, size=(W, H)) -> bytes:
    buf = io.BytesIO()
    kwargs = {"exif": exif} if exif is not None else {}
    Image.new("RGB", size, color).save(buf, format=fmt, **kwargs)
    return buf.getvalue()


def model_handler(kw):
    if kw["model"] == "sam-3.1":
        if concept_of(kw) == "cardboard box":
            return sam_line(sam_record(0, 20, 30, 49, 59, w=W, h=H, c=0.95))
        return sam_line(sam_record(0, 0, 50, 99, 59, w=W, h=H, c=0.8))
    return json.dumps({"assessments": [{"index": 0, "damaged": True, "damage_type": "dent",
                                        "severity": "low", "confidence": 0.7, "reason": "Side dent"}]})


@pytest.fixture(autouse=True)
def cache_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(scan_mod, "CACHE_DIR", tmp_path / "cache")
    return tmp_path / "cache"


@pytest.fixture
def fake(monkeypatch):
    return install_fake_model(monkeypatch, model_handler)


def post(data: bytes, mime="image/png"):
    return client.post("/scan", files={"image": ("photo", data, mime)})


def test_scan_success(fake, cache_dir):
    res = post(image_bytes())
    assert res.status_code == 200
    body = res.json()
    assert body["image_size"] == {"width": W, "height": H}
    assert {o["id"]: o["label"] for o in body["objects"]} == {"box_0": "box", "walkway_0": "walkway"}
    by_type = {f["type"]: f for f in body["findings"]}
    assert by_type["box_damage"]["severity"] == "low"
    # Box rows 30..59 overlap the walkway rows 50..59: 10/30 of its area.
    assert by_type["walkway_blocked"]["severity"] == "medium"
    assert by_type["walkway_blocked"]["confidence"] == pytest.approx(0.8)
    assert set(body["timings_ms"]) >= {"segment", "inspect", "hazards"}
    assert (cache_dir / f"{body['scan_id']}.json").is_file()
    assert len(fake.calls) == 3  # two SAM concepts + one Muse inspection


def test_scan_cache_replay(fake):
    data = image_bytes()
    first = post(data).json()
    n = len(fake.calls)
    second = post(data)
    assert second.status_code == 200
    assert second.json() == first
    assert len(fake.calls) == n

    got = client.get(f"/scan/{first['scan_id']}")
    assert got.status_code == 200 and got.json() == first


def test_scan_id_is_sha256_prefix(fake):
    data = image_bytes()
    assert post(data).json()["scan_id"] == hashlib.sha256(data).hexdigest()[:16]


def test_unsupported_type_415(fake):
    assert post(b"GIF89a", "image/gif").status_code == 415
    assert fake.calls == []


def test_too_large_413(fake, monkeypatch):
    monkeypatch.setattr(main, "MAX_UPLOAD_BYTES", 100)
    assert post(image_bytes()).status_code == 413
    assert fake.calls == []


def test_undecodable_422(fake):
    assert post(b"definitely not an image").status_code == 422
    assert fake.calls == []


def test_mismatched_content_type_422(fake):
    assert post(image_bytes("JPEG"), "image/png").status_code == 422


def test_model_error_502_does_not_leak(monkeypatch):
    err = openai.APIConnectionError(request=httpx.Request("POST", "https://api.meta.ai/v1/responses"))
    install_fake_model(monkeypatch, lambda kw: err)
    res = post(image_bytes())
    assert res.status_code == 502
    assert "test-key" not in res.text and "Connection" not in res.text


def test_get_unknown_scan_404():
    assert client.get("/scan/0123456789abcdef").status_code == 404


@pytest.mark.parametrize("bad", ["..%2F..%2Fetc%2Fpasswd", "0123456789ABCDEF", "abc", "0123456789abcdef0", "....json"])
def test_get_scan_rejects_bad_ids(bad):
    assert client.get(f"/scan/{bad}").status_code in (404, 422)


def test_get_scan_traversal_never_reads_outside_cache(cache_dir, tmp_path):
    (tmp_path / "secret.json").write_text("{}")
    # Encoded slashes never reach the handler; slash-free tricks fail the id pattern.
    assert client.get("/scan/..%2Fsecret").status_code == 404
    assert client.get("/scan/..secret").status_code == 422


def test_exif_stripped_and_orientation_applied(fake):
    exif = Image.Exif()
    exif[0x0112] = 6  # rotate 90° CW on display
    exif[0x010F] = "SecretCam"
    data = image_bytes("JPEG", exif=exif, size=(W, H))

    img = scan_mod.load_image(data, "image/jpeg")
    assert img.size == (H, W)
    reencoded = Image.open(io.BytesIO(scan_mod.encode_jpeg(img)))
    assert len(reencoded.getexif()) == 0

    post(data, "image/jpeg")
    sent_url = fake.calls[0]["input"][0]["content"][1]["image_url"]
    sent = base64.b64decode(sent_url.split(",", 1)[1])
    assert b"SecretCam" not in sent
    assert len(Image.open(io.BytesIO(sent)).getexif()) == 0


def test_large_image_downscaled(fake, monkeypatch):
    monkeypatch.setattr(scan_mod, "MAX_SIDE", 50)
    img = scan_mod.load_image(image_bytes(), "image/png")
    assert max(img.size) == 50
