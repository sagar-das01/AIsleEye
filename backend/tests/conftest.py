import os

import pytest

# Must be set before app modules import settings; real env vars beat the local .env file.
os.environ.update(
    MODEL_API_KEY="test-key",
    MODEL_API_BASE_URL="https://api.meta.ai/v1",
    MUSE_MODEL="muse-spark-1.3",
    SAM_MODEL="sam-3.1",
    CORS_ORIGINS='["http://localhost:3000"]',
)

from app.clients import get_async_client, get_client  # noqa: E402
from app.config import get_settings  # noqa: E402


@pytest.fixture(autouse=True)
def _clear_caches():
    yield
    get_settings.cache_clear()
    get_client.cache_clear()
    get_async_client.cache_clear()
