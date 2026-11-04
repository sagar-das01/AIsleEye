import hashlib
import io

import numpy as np
import pytest
from PIL import Image

from scripts import audit_labels, prepare_dataset
from scripts.dataset_utils import FIELDS, dhash, distance, read_manifest, split_for, write_manifest


def photo(seed: int, size=(96, 72)) -> Image.Image:
    rng = np.random.default_rng(seed)
    coarse = rng.integers(0, 256, (6, 8, 3), dtype=np.uint8)
    return Image.fromarray(coarse).resize(size, Image.Resampling.BICUBIC)


def save(directory, name, img, fmt="JPEG", **kw):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    img.save(path, format=fmt, **kw)
    return path


def test_dhash_survives_rescaling_and_recompression_but_not_a_different_photo():
    a = photo(1)
    buf = io.BytesIO()
    a.resize((48, 36)).save(buf, format="JPEG", quality=60)
    assert distance(dhash(a), dhash(Image.open(buf))) <= 4
    assert distance(dhash(a), dhash(photo(2))) > 10


def test_split_is_stable_and_mixed():
    shas = [hashlib.sha256(str(i).encode()).hexdigest()[:16] for i in range(200)]
    assert [split_for(s) for s in shas] == [split_for(s) for s in shas]
    assert {split_for(s) for s in shas} == {"dev", "eval"}


def test_prepare_preprocesses_hash_names_and_writes_a_manifest(tmp_path):
    src, out = tmp_path / "src", tmp_path / "out"
    exif = Image.Exif()
    exif[0x010F] = "SecretCam"
    save(src, "a.jpg", photo(1), exif=exif)
    save(src, "b.png", photo(2), fmt="PNG")
    (src / "notes.txt").write_text("not an image")
    counts = prepare_dataset.prepare(src, out, source="own", license="own work", label="intact")
    assert counts == {"added": 2, "invalid": 0, "duplicate": 0}
    rows = read_manifest(out / "manifest.csv")
    assert list(rows[0]) == FIELDS and {r["label"] for r in rows} == {"intact"} and {r["source"] for r in rows} == {"own"}
    for r in rows:
        data = (out / "processed" / r["file"]).read_bytes()
        assert r["file"] == hashlib.sha256(data).hexdigest()[:16] + ".jpg"
        assert b"SecretCam" not in data and Image.open(io.BytesIO(data)).format == "JPEG"


def test_prepare_drops_near_duplicates_and_unreadable_files(tmp_path):
    src, out = tmp_path / "src", tmp_path / "out"
    a = photo(1)
    save(src, "a.jpg", a)
    save(src, "a_small.jpg", a.resize((48, 36)), quality=60)
    (src / "broken.jpg").write_bytes(b"\xff\xd8 not really")
    counts = prepare_dataset.prepare(src, out, source="own", license="own work")
    assert counts["added"] == 1 and counts["duplicate"] == 1 and counts["invalid"] == 1


def test_prepare_respects_the_sample_size_and_does_not_duplicate_on_a_second_run(tmp_path):
    src, out = tmp_path / "src", tmp_path / "out"
    for i in range(6):
        save(src, f"{i}.jpg", photo(10 + i))
    assert prepare_dataset.prepare(src, out, source="s", license="l", n=3)["added"] == 3
    assert prepare_dataset.prepare(src, out, source="s", license="l", n=3)["added"] == 3
    rows = read_manifest(out / "manifest.csv")
    assert len(rows) == 6 and len({r["file"] for r in rows}) == 6


@pytest.mark.parametrize(("kwargs", "match"), [({"license": " "}, "license"), ({"license": "x", "label": "huge"}, "label")])
def test_prepare_refuses_missing_license_and_unknown_labels(tmp_path, kwargs, match):
    with pytest.raises(ValueError, match=match):
        prepare_dataset.prepare(tmp_path, tmp_path / "out", source="s", **kwargs)


def build(tmp_path, labels=("damaged", "intact", "blocked", "clear")):
    out = tmp_path / "ds"
    for i, label in enumerate(labels):
        save(tmp_path / "src", f"{i}.jpg", photo(50 + i))
    prepare_dataset.prepare(tmp_path / "src", out, source="s", license="l", label="intact")
    rows = read_manifest(out / "manifest.csv")
    for r, label in zip(rows, labels, strict=True):
        r["label"] = label
    write_manifest(out / "manifest.csv", rows)
    return out, rows


def test_a_clean_dataset_has_no_issues_and_reports_balance(tmp_path):
    out, rows = build(tmp_path)
    issues, balance = audit_labels.audit(out)
    assert issues == []
    assert sum(sum(c.values()) for c in balance.values()) == len(rows)


def test_audit_reports_bad_labels_splits_missing_files_and_tampered_content(tmp_path):
    out, rows = build(tmp_path)
    rows[0]["label"] = "broken"
    rows[1]["split"] = "test"
    rows[2]["license"] = ""
    (out / "processed" / rows[3]["file"]).write_bytes(b"x")
    write_manifest(out / "manifest.csv", rows)
    messages = " | ".join(i["issue"] for i in audit_labels.audit(out)[0])
    assert "label 'broken'" in messages and "split 'test'" in messages and "no license" in messages and "hash name" in messages
    assert "not a readable image" in messages
    (out / "processed" / rows[0]["file"]).unlink()
    assert any(i["issue"] == "file is missing" for i in audit_labels.audit(out)[0])


def test_audit_finds_near_duplicates_across_splits(tmp_path):
    out = tmp_path / "ds"
    a = photo(7)
    shas = []
    for name, img in (("a", a), ("b", a.resize((48, 36)))):
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=70 if name == "b" else 92)
        data = buf.getvalue()
        sha = hashlib.sha256(data).hexdigest()[:16]
        (out / "processed").mkdir(parents=True, exist_ok=True)
        (out / "processed" / f"{sha}.jpg").write_bytes(data)
        shas.append(sha)
    write_manifest(out / "manifest.csv", [
        {"file": f"{shas[0]}.jpg", "source": "s", "license": "l", "label": "intact", "split": "dev"},
        {"file": f"{shas[1]}.jpg", "source": "s", "license": "l", "label": "intact", "split": "eval"},
    ])
    [issue] = audit_labels.audit(out)[0]
    assert "near-duplicate" in issue["issue"] and "another split" in issue["issue"]


def test_audit_rejects_a_manifest_with_the_wrong_columns(tmp_path):
    (tmp_path / "manifest.csv").write_text("name,tag\nx,y\n")
    [issue] = audit_labels.audit(tmp_path)[0]
    assert "columns" in issue["issue"]


def test_quarantine_moves_the_file_and_excludes_it_from_later_audits(tmp_path):
    out, rows = build(tmp_path)
    bad = rows[0]["file"]
    assert audit_labels.quarantine(out, [bad]) == [bad]
    assert (out / "quarantine" / bad).is_file() and not (out / "processed" / bad).exists()
    assert bad in audit_labels.read_exclusions(out)
    assert audit_labels.audit(out)[0] == []


def test_quarantine_refuses_unknown_names_and_paths(tmp_path):
    out, _ = build(tmp_path)
    with pytest.raises(ValueError):
        audit_labels.quarantine(out, ["../../etc/passwd"])
    with pytest.raises(ValueError):
        audit_labels.quarantine(out, ["nope.jpg"])


def test_main_returns_nonzero_when_issues_exist_and_writes_the_review_csv(tmp_path, capsys):
    out, rows = build(tmp_path)
    assert audit_labels.main(["--root", str(out)]) == 0
    rows[0]["label"] = "broken"
    write_manifest(out / "manifest.csv", rows)
    assert audit_labels.main(["--root", str(out)]) == 1
    assert "broken" in (out / "review" / "issues.csv").read_text()
