"""
test_04_storage.py — MinIO / S3 file-storage reachability tests.

Plane routes /uploads/ through the internal Caddy proxy (plane-proxy)
to the MinIO container.  These tests validate that the storage endpoint
is reachable and functional through the nginx layer.
"""

import pytest
import requests


class TestStorage:
    """MinIO / S3-compatible storage reachability tests via nginx."""

    def test_uploads_route_responds(self, session: requests.Session, base_url: str):
        """
        GET /uploads/ through nginx must not produce a 5xx error.
        MinIO typically returns 403 or 404 for directory listings
        (access-denied), which is perfectly acceptable.
        """
        resp = session.get(f"{base_url}/uploads/", allow_redirects=True, timeout=30)
        assert resp.status_code < 500, (
            f"/uploads/ returned server error: HTTP {resp.status_code}\n"
            f"{resp.text[:500]}"
        )

    def test_minio_health_directly(self, session: requests.Session, minio_url: str):
        """
        MinIO exposes a live-probe endpoint at /minio/health/live.
        This is checked directly inside the Docker network (not via nginx)
        to confirm the MinIO container itself is operational.
        """
        resp = session.get(f"{minio_url}/minio/health/live", timeout=15)
        assert resp.status_code == 200, (
            f"MinIO health probe returned HTTP {resp.status_code} — "
            "the storage service may not be running."
        )

    def test_minio_ready_probe(self, session: requests.Session, minio_url: str):
        """MinIO readiness probe must return 200."""
        resp = session.get(f"{minio_url}/minio/health/ready", timeout=15)
        assert resp.status_code == 200, (
            f"MinIO readiness probe returned HTTP {resp.status_code}"
        )

    def test_uploads_bucket_accessible_via_proxy(
        self, session: requests.Session, base_url: str
    ):
        """
        Attempting to GET the uploads bucket via the nginx → plane-proxy path
        must not result in a 5xx.  A 403/404 from MinIO is expected and
        acceptable (bucket exists but access requires signed URLs).
        """
        resp = session.get(
            f"{base_url}/uploads/",
            allow_redirects=True,
            timeout=30,
        )
        assert resp.status_code not in (500, 502, 503, 504), (
            f"Proxy to storage returned server error: {resp.status_code}"
        )
