"""
conftest.py — Shared pytest fixtures and service-readiness helpers.

All tests in this suite must:
  - Go through the nginx layer (BASE_URL env var).
  - Never reach internal services directly (except for DEBUG assertions).
  - Wait patiently for services to become ready before making assertions.
"""

import os
import time
import pytest
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from support import build_workspace

# ----------------------------------------------------------------
# Configuration — supplied via environment (set in docker-compose.test.yml)
# ----------------------------------------------------------------
BASE_URL        = os.environ.get("BASE_URL",           "http://nginx-test:80").rstrip("/")
API_URL         = os.environ.get("API_URL",            "http://api:8000").rstrip("/")
MINIO_URL       = os.environ.get("MINIO_URL",          "http://plane-minio:9000").rstrip("/")
LIVE_URL        = os.environ.get("LIVE_URL",           "http://live:3000").rstrip("/")
TEST_TIMEOUT    = int(os.environ.get("TEST_TIMEOUT",   "60"))
RETRY_INTERVAL  = int(os.environ.get("TEST_RETRY_INTERVAL", "5"))


# ----------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------
def make_session(total_retries: int = 3, backoff_factor: float = 0.5) -> requests.Session:
    """Return a requests.Session with retry logic baked in."""
    session = requests.Session()
    retry = Retry(
        total=total_retries,
        backoff_factor=backoff_factor,
        status_forcelist=[502, 503, 504],
        allowed_methods=["GET", "HEAD", "POST"],
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


def wait_for_url(
    url: str,
    *,
    timeout: int = TEST_TIMEOUT,
    interval: int = RETRY_INTERVAL,
    expected_statuses: tuple = (200, 301, 302, 307, 308),
    label: str | None = None,
) -> None:
    """
    Block until *url* returns one of *expected_statuses* or *timeout*
    seconds elapse.  Raises pytest.fail() on timeout.

    This function is intentionally lenient about redirects because some
    Plane routes return 302 on first access (e.g. /god-mode/).
    """
    label = label or url
    session = make_session()
    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        try:
            resp = session.get(url, allow_redirects=False, timeout=10)
            if resp.status_code in expected_statuses:
                return
        except requests.RequestException:
            pass
        time.sleep(interval)

    pytest.fail(
        f"Service at {label!r} did not become ready within {timeout}s "
        f"(expected HTTP {expected_statuses})."
    )


# ----------------------------------------------------------------
# Session-scoped fixtures (started once per test session)
# ----------------------------------------------------------------
@pytest.fixture(scope="session")
def session() -> requests.Session:
    """A shared requests session with retry logic."""
    return make_session()


@pytest.fixture(scope="session", autouse=True)
def wait_for_nginx(session):
    """
    Session-wide fixture: block until the nginx layer is ready.
    This must succeed before any test runs.

    Set SKIP_NGINX_WAIT=1 to skip the wait — used when running only the
    pure unit tests (test_06..09) without the full Plane stack.
    """
    if os.environ.get("SKIP_NGINX_WAIT") == "1":
        return
    wait_for_url(f"{BASE_URL}/", label="nginx → plane", timeout=TEST_TIMEOUT)


@pytest.fixture()
def workspace(tmp_path):
    """Isolated project copy with a fake docker binary on PATH."""
    return build_workspace(tmp_path)


@pytest.fixture(scope="session")
def base_url() -> str:
    return BASE_URL


@pytest.fixture(scope="session")
def api_url() -> str:
    return API_URL


@pytest.fixture(scope="session")
def minio_url() -> str:
    return MINIO_URL


@pytest.fixture(scope="session")
def live_url() -> str:
    return LIVE_URL
