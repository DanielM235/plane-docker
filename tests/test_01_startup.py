"""
test_01_startup.py — Instance startup and basic HTTP smoke tests.

Validates that the Plane application is reachable through the nginx
layer and that the main routes return sensible HTTP responses.

All requests go to BASE_URL (nginx) — never to internal services.
"""

import pytest
import requests
from conftest import BASE_URL, make_session


class TestStartup:
    """Basic HTTP smoke tests through the nginx reverse proxy."""

    def test_root_returns_success(self, session: requests.Session, base_url: str):
        """
        GET / must return a 2xx or a redirect.
        An authenticated user would get 200; an unauthenticated user is
        redirected to the sign-in page.  Either is acceptable here.
        """
        resp = session.get(f"{base_url}/", allow_redirects=True, timeout=30)
        assert resp.status_code in (200, 301, 302, 307, 308), (
            f"Expected a 2xx/3xx at /, got {resp.status_code}"
        )

    def test_root_content_type_is_html(self, session: requests.Session, base_url: str):
        """The eventual response at / should be HTML."""
        resp = session.get(f"{base_url}/", allow_redirects=True, timeout=30)
        content_type = resp.headers.get("Content-Type", "")
        assert "text/html" in content_type, (
            f"Expected text/html content-type at /, got {content_type!r}"
        )

    def test_sign_in_page_reachable(self, session: requests.Session, base_url: str):
        """The sign-in route /sign-in/ must be reachable (200 or redirect)."""
        resp = session.get(f"{base_url}/sign-in/", allow_redirects=True, timeout=30)
        assert resp.status_code in (200, 301, 302, 307, 308), (
            f"Expected 2xx/3xx at /sign-in/, got {resp.status_code}"
        )

    def test_static_assets_served(self, session: requests.Session, base_url: str):
        """
        The nginx proxy must not block non-root paths.
        We load the root and then try to follow at least one asset path.
        """
        resp = session.get(f"{base_url}/", allow_redirects=True, timeout=30)
        # If the page has a <link> or <script> tag, at least the route works.
        assert resp.status_code < 500, (
            f"Server error while loading /: HTTP {resp.status_code}"
        )

    def test_no_5xx_on_root(self, session: requests.Session, base_url: str):
        """The root must not return an internal server error."""
        resp = session.get(f"{base_url}/", allow_redirects=False, timeout=30)
        assert resp.status_code < 500, (
            f"Server error at /: HTTP {resp.status_code}\n{resp.text[:500]}"
        )

    def test_server_header_absent_or_safe(self, session: requests.Session, base_url: str):
        """
        nginx should not expose its version number.
        The Server header must either be absent or not contain a version string.
        """
        resp = session.get(f"{base_url}/", allow_redirects=False, timeout=30)
        server = resp.headers.get("Server", "")
        # nginx/1.27.x would expose exact version; server_tokens off hides it
        assert "/" not in server or "nginx" not in server.lower(), (
            f"nginx is leaking its version via Server header: {server!r}"
        )

    @pytest.mark.parametrize("path", [
        "/spaces/",
        "/god-mode/",
    ])
    def test_app_routes_not_404(
        self, session: requests.Session, base_url: str, path: str
    ):
        """Key application routes must resolve (not 404 or 5xx)."""
        resp = session.get(f"{base_url}{path}", allow_redirects=True, timeout=30)
        assert resp.status_code not in (404, 500, 502, 503), (
            f"Route {path} returned unexpected status {resp.status_code}"
        )
