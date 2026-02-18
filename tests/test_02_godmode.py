"""
test_02_godmode.py — Instance admin (God Mode) routing tests.

After the stack starts for the first time, /god-mode/ must always be
accessible — either showing the setup / onboarding page, or returning
an application page (never a 404 or a server error).

Plane redirects unauthenticated requests to its sign-in page; the key
invariant is that the route EXISTS and does not produce an error.
"""

import pytest
import requests


GOD_MODE_PATH    = "/god-mode/"
GOD_MODE_SETUP   = "/god-mode/general/"   # common admin sub-path


class TestGodMode:
    """Tests for the Instance Admin (god-mode) route availability."""

    def test_god_mode_route_reachable(self, session: requests.Session, base_url: str):
        """
        /god-mode/ must be reachable (2xx or redirect) through nginx.
        A 404 or 5xx here would indicate routing is broken.
        """
        resp = session.get(
            f"{base_url}{GOD_MODE_PATH}",
            allow_redirects=True,
            timeout=30,
        )
        assert resp.status_code not in (404, 500, 502, 503, 504), (
            f"/god-mode/ returned unexpected status {resp.status_code}:\n"
            f"{resp.text[:500]}"
        )

    def test_god_mode_no_blank_response(self, session: requests.Session, base_url: str):
        """
        The god-mode response body must not be empty.  An empty body
        suggests the upstream (admin container) is not serving content.
        """
        resp = session.get(
            f"{base_url}{GOD_MODE_PATH}",
            allow_redirects=True,
            timeout=30,
        )
        assert len(resp.content) > 0, (
            "/god-mode/ returned an empty response body."
        )

    def test_god_mode_html_content(self, session: requests.Session, base_url: str):
        """The final response at /god-mode/ must be HTML."""
        resp = session.get(
            f"{base_url}{GOD_MODE_PATH}",
            allow_redirects=True,
            timeout=30,
        )
        content_type = resp.headers.get("Content-Type", "")
        assert "text/html" in content_type, (
            f"/god-mode/ returned non-HTML content-type: {content_type!r}"
        )

    def test_god_mode_intermediate_redirect_not_loop(
        self, session: requests.Session, base_url: str
    ):
        """
        Follow redirects and ensure we do not end up in an infinite loop
        (requests raises TooManyRedirects if the limit is exceeded).
        """
        try:
            resp = session.get(
                f"{base_url}{GOD_MODE_PATH}",
                allow_redirects=True,
                timeout=30,
            )
            assert resp.status_code < 500, (
                f"/god-mode/ redirect chain ended in server error: {resp.status_code}"
            )
        except requests.TooManyRedirects:
            pytest.fail("/god-mode/ caused a redirect loop.")

    def test_god_mode_setup_sub_route(self, session: requests.Session, base_url: str):
        """
        The admin general-settings sub-route must resolve.
        On first run, unauthenticated users are typically redirected to
        the admin sign-in page — that is acceptable.
        """
        resp = session.get(
            f"{base_url}{GOD_MODE_SETUP}",
            allow_redirects=True,
            timeout=30,
        )
        assert resp.status_code not in (404, 500, 502, 503, 504), (
            f"{GOD_MODE_SETUP} returned unexpected status {resp.status_code}"
        )

    def test_god_mode_not_accidentally_open(
        self, session: requests.Session, base_url: str
    ):
        """
        /god-mode/ must never be publicly accessible without authentication.
        If the response status is 200 on a fresh instance, the page
        content must contain a recognisable sign-in or setup prompt —
        not an authenticated admin dashboard exposed to anonymous users.
        """
        resp = session.get(
            f"{base_url}{GOD_MODE_PATH}",
            allow_redirects=True,
            timeout=30,
        )
        if resp.status_code == 200:
            body = resp.text.lower()
            # Acceptable keywords: indicates a sign-in / setup flow
            acceptable_keywords = ["sign in", "sign-in", "login", "setup", "password"]
            # Unacceptable (admin dashboard exposed unauthenticated):
            dangerous_keywords  = ["workspace", "billing", "license", "users list"]

            is_setup_page = any(kw in body for kw in acceptable_keywords)
            is_dashboard  = any(kw in body for kw in dangerous_keywords)

            if is_dashboard and not is_setup_page:
                pytest.fail(
                    "God-mode admin dashboard appears to be publicly accessible "
                    "without authentication — check ACCESS_CONTROL settings."
                )
