"""
test_10_compose_audit.py — Static security/config audit of docker-compose.yml.

Parses the production Compose file and asserts the invariants that prevent
the regressions reported in production (missing live env vars, missing
healthchecks, missing resource limits).  Uses PyYAML.
"""

from pathlib import Path

import pytest
import yaml

COMPOSE_FILE = Path("/project/docker-compose.yml")

# Services that must all carry a deploy.resources.limits block.
ALL_SERVICES = [
    "migrator",
    "api",
    "worker",
    "beat-worker",
    "web",
    "space",
    "admin",
    "live",
    "plane-proxy",
    "plane-db",
    "plane-redis",
    "plane-mq",
    "plane-minio",
]

# Services whose environment must include the live-server secret so the
# backend and the realtime service share the same key.
BACKEND_SERVICES = ["migrator", "api", "worker", "beat-worker"]


@pytest.fixture(scope="module")
def compose() -> dict:
    return yaml.safe_load(COMPOSE_FILE.read_text())


def _env(svc: dict) -> dict:
    return svc.get("environment", {}) or {}


def _limits(svc: dict) -> dict:
    return svc.get("deploy", {}).get("resources", {}).get("limits", {}) or {}


class TestLiveService:
    """The live service must have everything it needs to boot cleanly."""

    def test_live_has_server_secret(self, compose):
        assert "LIVE_SERVER_SECRET_KEY" in _env(compose["services"]["live"])

    def test_live_has_redis_config(self, compose):
        env = _env(compose["services"]["live"])
        assert "REDIS_URL" in env, "live must have REDIS_URL"
        assert "REDIS_HOST" in env
        assert "REDIS_PORT" in env

    def test_live_depends_on_redis_healthy(self, compose):
        deps = compose["services"]["live"].get("depends_on", {})
        assert deps.get("plane-redis", {}).get("condition") == "service_healthy"

    def test_live_has_healthcheck(self, compose):
        assert compose["services"]["live"].get("healthcheck", {}).get("test")


class TestBackendSecret:
    """Every backend service must share LIVE_SERVER_SECRET_KEY with live."""

    @pytest.mark.parametrize("service", BACKEND_SERVICES)
    def test_backend_has_server_secret(self, compose, service):
        assert "LIVE_SERVER_SECRET_KEY" in _env(compose["services"][service])


class TestHealthchecks:
    """No long-running service may ship without a healthcheck."""

    @pytest.mark.parametrize("service", ["worker", "beat-worker", "live"])
    def test_service_has_healthcheck(self, compose, service):
        hc = compose["services"][service].get("healthcheck", {})
        assert hc.get("test"), f"{service} must define a healthcheck"


class TestResourceLimits:
    """Every service must be capped to prevent runaway CPU/memory/pids."""

    @pytest.mark.parametrize("service", ALL_SERVICES)
    def test_service_has_cpu_memory_pids_limits(self, compose, service):
        limits = _limits(compose["services"][service])
        assert "cpus" in limits, f"{service} is missing a CPU limit"
        assert "memory" in limits, f"{service} is missing a memory limit"
        assert "pids" in limits, f"{service} is missing a pids limit"


class TestHardening:
    """Every service must be hardened (no-new-privileges + cap_drop ALL)."""

    @pytest.mark.parametrize("service", ALL_SERVICES)
    def test_security_opt(self, compose, service):
        opts = compose["services"][service].get("security_opt", [])
        assert "no-new-privileges:true" in opts, f"{service} missing security_opt"

    @pytest.mark.parametrize("service", ALL_SERVICES)
    def test_cap_drop_all(self, compose, service):
        caps = compose["services"][service].get("cap_drop", [])
        assert "ALL" in caps, f"{service} must drop all capabilities"


class TestSecurityDocs:
    """The audit and secret-rotation guides must exist."""

    def test_security_audit_doc_exists(self):
        assert (Path("/project/docs/SECURITY_AUDIT.md")).exists()

    def test_secret_rotation_doc_exists(self):
        assert (Path("/project/docs/SECRET_ROTATION.md")).exists()
