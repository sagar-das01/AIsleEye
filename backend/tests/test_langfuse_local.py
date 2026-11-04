import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from app.config import Settings

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "infra" / "langfuse" / "docker-compose.yml"
SCRIPT = ROOT / "scripts" / "langfuse-local.sh"


@pytest.fixture(scope="module")
def compose() -> dict:
    return yaml.safe_load(COMPOSE.read_text())


def test_the_stack_has_every_service_langfuse_needs(compose):
    assert set(compose["services"]) == {"langfuse-web", "langfuse-worker", "postgres", "clickhouse", "redis", "minio"}


def test_every_published_port_is_bound_to_localhost(compose):
    for name, service in compose["services"].items():
        for port in service.get("ports", []):
            assert str(port).startswith("127.0.0.1:"), f"{name} publishes {port} on all interfaces"


def test_only_the_web_ui_and_media_store_are_published(compose):
    published = {name for name, s in compose["services"].items() if s.get("ports")}
    assert published == {"langfuse-web", "minio"}


def test_no_secret_is_hard_coded_in_the_compose_file():
    text = COMPOSE.read_text()
    for var in ("POSTGRES_PASSWORD", "CLICKHOUSE_PASSWORD", "MINIO_ROOT_PASSWORD", "REDIS_AUTH", "NEXTAUTH_SECRET", "SALT",
                "ENCRYPTION_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_USER_PASSWORD"):
        assert "${" + var + ":?" in text, f"{var} must be required from infra/langfuse/.env, never defaulted"
    assert not re.search(r"(PASSWORD|SECRET|KEY|SALT): (?!\$\{)[A-Za-z0-9]{8,}", text)


def test_telemetry_is_off_and_signup_is_closed(compose):
    web = compose["services"]["langfuse-web"]["environment"]
    assert web["TELEMETRY_ENABLED"] == "false" and web["AUTH_DISABLE_SIGNUP"] == "true"
    assert compose["services"]["langfuse-worker"]["environment"]["TELEMETRY_ENABLED"] == "false"


def test_images_are_versioned_except_the_one_minio_build_that_has_no_tags(compose):
    for name, service in compose["services"].items():
        image = service["image"]
        assert ":" in image.rsplit("/", 1)[-1], f"{name} image has no tag"
        if name != "minio":
            assert not image.endswith(":latest")


def test_stateful_services_use_named_volumes(compose):
    assert {"langfuse_postgres_data", "langfuse_clickhouse_data", "langfuse_minio_data"} <= set(compose["volumes"])


def test_web_waits_for_every_dependency_to_be_healthy(compose):
    deps = compose["services"]["langfuse-web"]["depends_on"]
    assert set(deps) == {"postgres", "minio", "redis", "clickhouse"}
    assert all(d["condition"] == "service_healthy" for d in deps.values())
    for name in deps:
        assert "healthcheck" in compose["services"][name]


def test_compose_refuses_to_start_without_generated_secrets(tmp_path):
    if shutil.which("docker") is None:
        pytest.skip("docker not installed")
    isolated = tmp_path / "docker-compose.yml"  # compose reads a .env next to the file, so test a copy without one
    isolated.write_text(COMPOSE.read_text())
    result = subprocess.run(
        ["docker", "compose", "-f", str(isolated), "config", "-q"], capture_output=True, text=True, env={"PATH": "/usr/bin:/bin:/usr/local/bin"}
    )
    assert result.returncode != 0 and "scripts/langfuse-local.sh up" in result.stderr


def test_script_is_valid_strict_bash_and_executable():
    assert subprocess.run(["bash", "-n", str(SCRIPT)]).returncode == 0
    text = SCRIPT.read_text()
    assert "set -euo pipefail" in text and SCRIPT.stat().st_mode & 0o111


def test_script_rejects_unknown_commands():
    result = subprocess.run(["bash", str(SCRIPT), "explode"], capture_output=True, text=True)
    assert result.returncode == 2 and "usage" in result.stderr


def test_script_writes_secrets_with_owner_only_permissions():
    assert "umask 077" in SCRIPT.read_text()


def test_generated_secrets_file_is_git_ignored():
    if shutil.which("git") is None or not (ROOT / ".git").exists():
        pytest.skip("needs a git checkout")
    assert subprocess.run(["git", "check-ignore", "-q", "infra/langfuse/.env"], cwd=ROOT).returncode == 0


def test_the_app_defaults_to_the_local_instance_not_the_cloud():
    s = Settings(_env_file=None)
    assert s.langfuse_base_url == "http://localhost:3000" and s.langfuse_enabled is False
    assert "cloud.langfuse.com" not in (ROOT / ".env.example").read_text()
