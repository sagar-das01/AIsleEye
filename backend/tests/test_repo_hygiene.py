import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None or not (ROOT / ".git").exists(),
    reason="needs a git checkout",
)


def git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)


def tracked() -> list[str]:
    return git("ls-files", "-z").stdout.split("\0")[:-1]


@pytest.mark.parametrize(
    "path",
    [".env", "plan/plan.md", "backend/.venv/x", "backend/data/smoke/sam_raw.txt", "frontend/node_modules/x", "frontend/.next/x"],
)
def test_sensitive_and_generated_paths_are_ignored(path):
    assert git("check-ignore", "-q", path).returncode == 0, f"{path} must be git-ignored"


def test_env_file_is_not_tracked():
    assert ".env" not in tracked()


def test_env_example_is_not_ignored():
    assert git("check-ignore", "-q", ".env.example").returncode != 0


def test_no_generated_or_vendored_files_are_tracked():
    bad = [p for p in tracked() if re.search(r"(^|/)(\.venv|node_modules|__pycache__|\.next)/|\.pyc$|\.tsbuildinfo$", p)]
    assert not bad


def test_no_large_files_are_tracked():
    big = [p for p in tracked() if (ROOT / p).is_file() and (ROOT / p).stat().st_size > 2 * 1024 * 1024]
    assert not big, f"large files tracked: {big}"


def test_no_real_secrets_are_tracked():
    this_file = Path(__file__).relative_to(ROOT).as_posix()
    patterns = [
        re.compile(r"MODEL_API_KEY\s*=\s*['\"]?(?!test-key|your|<|xxx|changeme|\$)[A-Za-z0-9_\-]{16,}"),
        re.compile(r"\b(sk|pk)-[A-Za-z0-9]{20,}"),
        re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}"),
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
        re.compile(r"AKIA[0-9A-Z]{16}"),
    ]
    hits = []
    for rel in tracked():
        path = ROOT / rel
        if rel == this_file or not path.is_file() or path.stat().st_size > 1_000_000:
            continue
        try:
            text = path.read_text()
        except UnicodeDecodeError:
            continue
        hits += [f"{rel}: {p.pattern[:20]}" for p in patterns if p.search(text)]
    assert not hits, hits


def test_local_user_paths_are_not_committed():
    this_file = Path(__file__).relative_to(ROOT).as_posix()
    hits = []
    for rel in tracked():
        path = ROOT / rel
        if rel == this_file or not path.is_file() or path.suffix in {".ico", ".png", ".jpg", ".svg", ".webp"} or rel.endswith("package-lock.json"):
            continue
        try:
            text = path.read_text()
        except UnicodeDecodeError:
            continue
        if re.search(r"/Users/[a-z0-9._-]+/|/home/[a-z0-9._-]+/", text, re.I):
            hits.append(rel)
    assert not hits, f"absolute local paths committed in {hits}"


def test_dependencies_are_declared_with_minimum_versions():
    for name in ("requirements.txt", "requirements-dev.txt"):
        lines = [ln.strip() for ln in (ROOT / "backend" / name).read_text().splitlines() if ln.strip() and not ln.startswith(("#", "-r"))]
        assert lines and all(re.search(r"[<>=~]=?\d", ln) for ln in lines), f"unpinned requirement in {name}"


def test_dev_requirements_include_runtime_requirements():
    assert "-r requirements.txt" in (ROOT / "backend" / "requirements-dev.txt").read_text()
