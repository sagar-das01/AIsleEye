import os

import pytest

LIVE = os.environ.get("RUN_LIVE") == "1"

# Must be set before app modules import settings; real env vars beat the local .env file.
if not LIVE:
    os.environ.update(
        MODEL_API_KEY="test-key",
        MODEL_API_BASE_URL="https://api.meta.ai/v1",
        MUSE_MODEL="muse-spark-1.3",
        SAM_MODEL="sam-3.1",
        CORS_ORIGINS='["http://localhost:3000"]',
        LANGFUSE_ENABLED="false",
        LANGFUSE_PUBLIC_KEY="",
        LANGFUSE_SECRET_KEY="",
    )

from app.clients import get_async_client, get_client  # noqa: E402
from app.config import get_settings  # noqa: E402


def pytest_collection_modifyitems(config, items):
    for item in items:
        is_live = "live" in item.keywords
        if is_live and not LIVE:
            item.add_marker(pytest.mark.skip(reason="live API test: run RUN_LIVE=1 pytest -m live"))
        elif LIVE and not is_live:
            item.add_marker(pytest.mark.skip(reason="RUN_LIVE=1 runs only the live tests"))


@pytest.fixture(autouse=True)
def _clear_caches():
    yield
    get_settings.cache_clear()
    get_client.cache_clear()
    get_async_client.cache_clear()


@pytest.fixture(autouse=True)
def _isolated_scores(tmp_path_factory, monkeypatch):
    # Without this, every test that runs an agent would append to the developer's real score log.
    from app import observability, scoring

    monkeypatch.setattr(scoring, "SCORES_PATH", tmp_path_factory.mktemp("scores") / "scores.jsonl")
    yield
    observability.shutdown()


@pytest.fixture(autouse=True)
def _authenticated_api(request, monkeypatch, tmp_path):
    from app import audit
    from app.auth import Grant, Role, User, current_user
    from app.main import app

    monkeypatch.setattr(audit, "AUDIT_PATH", tmp_path / "audit_log.jsonl")
    if not LIVE and request.node.get_closest_marker("rbac") is None:
        # Existing pipeline tests exercise their original behavior behind an explicit test-only identity.
        monkeypatch.setitem(
            app.dependency_overrides, current_user,
            lambda: User(subject="test-admin", display_name="Test admin", grants=[
                Grant(role=Role.ADMIN, warehouse_id="default"),
            ]),
        )
