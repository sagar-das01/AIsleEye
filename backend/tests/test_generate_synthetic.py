import pytest

from scripts import generate_synthetic as gen
from scripts.dataset_utils import LABELS, read_manifest


@pytest.mark.parametrize("label", LABELS)
def test_scene_geometry_matches_the_label(label):
    for seed in range(8):
        _, boxes = gen.build(label, seed)
        blocking = [b for b in boxes if gen.blocks_lane(b)]
        damaged = [b for b in boxes if b.damage]
        assert bool(blocking) == (label == "blocked")
        assert bool(damaged) == (label == "damaged")


def test_scenes_are_deterministic_and_vary_with_the_seed():
    a1, _ = gen.build("damaged", 3)
    a2, _ = gen.build("damaged", 3)
    b, _ = gen.build("damaged", 4)
    assert a1.tobytes() == a2.tobytes()
    assert a1.tobytes() != b.tobytes()


def test_unknown_label_is_rejected():
    with pytest.raises(ValueError):
        gen.build("wet", 0)


def test_generate_writes_a_labelled_manifest_and_is_idempotent(tmp_path):
    counts = gen.generate(tmp_path, count=3, seed=1)
    assert set(counts) == set(LABELS)
    rows = read_manifest(tmp_path / "manifest.csv")
    assert {r["label"] for r in rows} == set(LABELS)
    assert {r["source"] for r in rows} == {gen.SOURCE}
    assert all(r["license"] == gen.LICENSE and (tmp_path / "processed" / r["file"]).is_file() for r in rows)
    assert {r["split"] for r in rows} <= {"dev", "eval"}

    before = len(rows)
    gen.generate(tmp_path, count=3, seed=1)
    assert len(read_manifest(tmp_path / "manifest.csv")) == before


def test_main_validates_count_and_runs(tmp_path, capsys):
    with pytest.raises(SystemExit):
        gen.main(["--count", "0", "--out", str(tmp_path)])
    assert gen.main(["--count", "2", "--classes", "clear", "--out", str(tmp_path)]) == 0
    assert "clear:" in capsys.readouterr().out
    assert {r["label"] for r in read_manifest(tmp_path / "manifest.csv")} == {"clear"}
