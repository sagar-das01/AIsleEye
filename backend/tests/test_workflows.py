import re
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
SCRIPT = ROOT / ".github" / "scripts" / "next_version.sh"


def load(name: str) -> dict:
    doc = yaml.safe_load((WORKFLOWS / name).read_text())
    # YAML 1.1 parses the bare key `on` as boolean True.
    doc["on"] = doc.pop("on", doc.pop(True, None))
    return doc


def branch_prefixes() -> set[str]:
    text = (WORKFLOWS / "branch-name.yml").read_text()
    match = re.search(r"\^\(([a-z|]+)\)/", text)
    assert match, "branch-name.yml pattern not found"
    return set(match.group(1).split("|"))


def bump_for(prefix: str) -> str:
    out = subprocess.run(["bash", str(SCRIPT), f"{prefix}/x", "v1.0.0"], capture_output=True, text=True, check=True)
    return out.stdout.split()[0]


@pytest.mark.parametrize("name", ["ci.yml", "branch-name.yml", "release.yml"])
def test_workflow_files_parse_and_have_jobs(name):
    doc = load(name)
    assert doc["name"] and doc["on"] and doc["jobs"]
    for job in doc["jobs"].values():
        assert job["runs-on"]
        assert job.get("steps")


@pytest.mark.parametrize("name", ["ci.yml", "release.yml"])
def test_workflows_declare_least_privilege_permissions(name):
    doc = load(name)
    assert "permissions" in doc
    assert doc["permissions"]["contents"] in ("read", "write")
    if name == "ci.yml":
        assert doc["permissions"]["contents"] == "read"


def test_ci_runs_backend_and_frontend_jobs():
    jobs = load("ci.yml")["jobs"]
    assert {"backend", "frontend"} <= set(jobs)
    backend = " ".join(str(s.get("run", "")) for s in jobs["backend"]["steps"])
    frontend = " ".join(str(s.get("run", "")) for s in jobs["frontend"]["steps"])
    assert "pytest" in backend and "requirements-dev.txt" in backend
    for command in ("npm ci", "npm run lint", "npm run build"):
        assert command in frontend
    assert "npm test" in frontend or "npm run test" in frontend


def test_ci_never_uses_secrets_or_live_tests():
    text = (WORKFLOWS / "ci.yml").read_text()
    assert "secrets." not in text
    assert "RUN_LIVE" not in text and "MODEL_API_KEY" not in text


def test_ci_runs_on_pull_requests_to_any_base_branch():
    # Stacked PRs (feature -> test branch) must get checks too, so no base-branch filter.
    triggers = load("ci.yml")["on"]
    assert "pull_request" in triggers
    assert not (triggers["pull_request"] or {}).get("branches")
    assert triggers["push"]["branches"] == ["master"]


def test_ci_pins_runtime_versions():
    jobs = load("ci.yml")["jobs"]
    py = next(s for s in jobs["backend"]["steps"] if "setup-python" in str(s.get("uses", "")))
    node = next(s for s in jobs["frontend"]["steps"] if "setup-node" in str(s.get("uses", "")))
    assert str(py["with"]["python-version"]).startswith("3.13")
    assert str(node["with"]["node-version"]) == "22"


def test_ci_cancels_superseded_runs():
    assert load("ci.yml")["concurrency"]["cancel-in-progress"] is True


def test_release_only_runs_for_merged_prs_and_never_cancels():
    doc = load("release.yml")
    assert doc["on"]["pull_request"]["types"] == ["closed"]
    assert "merged == true" in doc["jobs"]["release"]["if"]
    assert doc["concurrency"]["cancel-in-progress"] is False


def test_release_passes_branch_through_env_not_inline():
    # Injecting github.head_ref directly into a script is a shell-injection risk.
    text = (WORKFLOWS / "release.yml").read_text()
    for step in load("release.yml")["jobs"]["release"]["steps"]:
        assert "head.ref" not in str(step.get("run", ""))
    assert "BRANCH: ${{ github.event.pull_request.head.ref }}" in text


def test_branch_name_check_does_not_inline_head_ref():
    for step in load("branch-name.yml")["jobs"]["check"]["steps"]:
        assert "head_ref" not in str(step.get("run", ""))


def test_every_allowed_branch_prefix_has_a_defined_bump():
    prefixes = branch_prefixes()
    assert {"feature", "fix", "docs", "test", "chore"} <= prefixes
    for prefix in prefixes:
        assert bump_for(prefix) in {"major", "minor", "patch", "none"}


def test_prefixes_that_release_are_all_allowed_by_the_branch_check():
    allowed = branch_prefixes()
    script = SCRIPT.read_text()
    releasing = set(re.findall(r"\b([a-z]+)/\*", script)) - {"*"}
    assert releasing <= allowed, f"script handles prefixes the branch check rejects: {releasing - allowed}"


@pytest.mark.parametrize(
    "branch",
    ["feature/ok-name", "fix/a.b_c", "test/unit-tests-and-ci"],
)
def test_branch_pattern_accepts_good_names(branch):
    pattern = re.search(r"pattern='([^']+)'", (WORKFLOWS / "branch-name.yml").read_text()).group(1)
    assert re.match(pattern, branch)


@pytest.mark.parametrize(
    "branch",
    ["master", "feature/", "feature/Upper", "feature/has space", "wip/thing", "feature/a/b", "Feature/x", "feature/x\n.."],
)
def test_branch_pattern_rejects_bad_names(branch):
    pattern = re.search(r"pattern='([^']+)'", (WORKFLOWS / "branch-name.yml").read_text()).group(1)
    assert not re.fullmatch(pattern, branch)


def test_readme_release_table_matches_the_script():
    rows = re.findall(r"^\| ((?:`[a-z]+/\*`(?:, )?)+) \| (major|minor|patch|none) \|", (ROOT / "README.md").read_text(), re.M)
    assert rows, "release table not found in README"
    for cell, bump in rows:
        for prefix in re.findall(r"`([a-z]+)/\*`", cell):
            assert bump_for(prefix) == bump, f"README says {prefix}/* -> {bump}"
