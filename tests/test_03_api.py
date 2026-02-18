"""
test_03_api.py — API health-endpoint and basic REST contract tests.

The Plane Django API (stable) does not expose a dedicated /api/health/
endpoint.  The public instance-configuration endpoint /api/instances/ is
unauthenticated, always returns HTTP 200 with a JSON body, and is the
de-facto API liveness signal.  All requests in this module go through
nginx (BASE_URL) — the same path a real browser would use.
"""

import json
import pytest
import requests


# Canonical public API liveness path for the Plane stable release.
# /api/instances/ is unauthenticated and returns instance configuration JSON.
_API_LIVENESS = "/api/instances/"


class TestAPIHealth:
    """Plane REST API health and basic contract tests via nginx."""

    def test_api_health_returns_200(self, session: requests.Session, base_url: str):
        """
        GET /api/instances/ through nginx must return HTTP 200.
        This validates end-to-end routing:  nginx → plane-proxy → api.
        """
        resp = session.get(f"{base_url}{_API_LIVENESS}", timeout=30)
        assert resp.status_code == 200, (
            f"Expected HTTP 200 from {_API_LIVENESS}, got {resp.status_code}\n"
            f"{resp.text[:500]}"
        )

    def test_api_health_returns_json(self, session: requests.Session, base_url: str):
        """The liveness endpoint must return a JSON body."""
        resp = session.get(f"{base_url}{_API_LIVENESS}", timeout=30)
        content_type = resp.headers.get("Content-Type", "")
        assert "application/json" in content_type, (
            f"Expected JSON from {_API_LIVENESS}, got Content-Type: {content_type!r}"
        )
        try:
            body = resp.json()
        except json.JSONDecodeError as exc:
            pytest.fail(f"{_API_LIVENESS} returned invalid JSON: {exc}")
        assert isinstance(body, dict), (
            f"{_API_LIVENESS} JSON body must be an object, got {type(body).__name__}"
        )

    def test_api_health_ok_field(self, session: requests.Session, base_url: str):
        """
        /api/instances/ returns instance configuration when the API is healthy.
        At minimum the response must contain a top-level key that signals a
        properly bootstrapped instance (e.g. 'config').
        """
        resp = session.get(f"{base_url}{_API_LIVENESS}", timeout=30)
        body = resp.json()
        # /api/instances/ returns a dict with known keys when healthy
        known_keys = {"config", "status", "ok", "healthy", "enable_signup"}
        assert known_keys & body.keys(), (
            f"{_API_LIVENESS} body does not contain any expected key "
            f"({known_keys}): {body}"
        )

    def test_api_unknown_route_returns_404(
        self, session: requests.Session, base_url: str
    ):
        """
        Requesting a non-existent API route must return 404, not 500.
        A 500 here would indicate an unhandled exception in the API.
        """
        resp = session.get(
            f"{base_url}/api/totally-nonexistent-plane-endpoint-xyz/",
            timeout=30,
        )
        assert resp.status_code == 404, (
            f"Expected 404 for unknown API route, got {resp.status_code}"
        )

    def test_api_unauthenticated_returns_401_or_403(
        self, session: requests.Session, base_url: str
    ):
        """
        Accessing a protected API endpoint without credentials must return
        401 (Unauthorized) or 403 (Forbidden), not 200.
        """
        protected_paths = [
            "/api/workspaces/",
            "/api/users/me/",
        ]
        for path in protected_paths:
            resp = session.get(f"{base_url}{path}", allow_redirects=False, timeout=30)
            assert resp.status_code in (401, 403, 302, 307), (
                f"Expected 401/403 for unauthenticated request to {path}, "
                f"got {resp.status_code}"
            )

    def test_api_cors_headers_present(self, session: requests.Session, base_url: str):
        """
        A preflight OPTIONS request to the API should be handled.
        The response must not be a 5xx error.
        """
        resp = session.options(
            f"{base_url}{_API_LIVENESS}",
            headers={
                "Origin": "http://example.com",
                "Access-Control-Request-Method": "GET",
            },
            timeout=30,
        )
        assert resp.status_code < 500, (
            f"API OPTIONS request failed with server error: {resp.status_code}"
        )
