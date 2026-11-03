import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / ".github" / "scripts" / "next_version.sh"


def run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["bash", str(SCRIPT), *args], capture_output=True, text=True)


@pytest.mark.parametrize(
    ("branch", "expected"),
    [
        ("major/api-v2", "major v2.0.0"),
        ("breaking/schema", "major v2.0.0"),
        ("release/demo", "minor v1.5.0"),
        ("feature/scan", "minor v1.5.0"),
        ("feat/overlay", "minor v1.5.0"),
        ("fix/mask-offset", "patch v1.4.3"),
        ("bug/cors", "patch v1.4.3"),
        ("bugfix/upload", "patch v1.4.3"),
        ("hotfix/crash", "patch v1.4.3"),
        ("chore/deps", "none v1.4.2"),
        ("test/unit-tests", "none v1.4.2"),
        ("docs/readme", "none v1.4.2"),
        ("master", "none v1.4.2"),
    ],
)
def test_bump_by_branch_prefix(branch, expected):
    result = run(branch, "v1.4.2")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == expected


def test_defaults_to_v0_0_0_when_no_tag():
    assert run("fix/first", "").stdout.strip() == "patch v0.0.1"


@pytest.mark.parametrize("bad", ["1.2.3", "v1.2", "vX.Y.Z", "v1.2.3-rc1"])
def test_rejects_invalid_current_version(bad):
    result = run("fix/x", bad)
    assert result.returncode != 0
    assert "invalid current version" in result.stderr


def test_requires_branch_argument():
    assert run().returncode != 0
