"""
test_11_db_first_boot.py — First-boot database initialization as non-root.

Plane's database runs as `user: postgres` with `cap_drop: [ALL]`.  This is
the trickiest part of the hardening: PostgreSQL's entrypoint normally runs
as root and chowns/chmods its data directory before dropping privileges.
Running the container directly as `postgres` must therefore still be able to
initialise a **brand-new volume** (initdb + first start) with no admin
rights at all.

This test talks to the Docker daemon through the mounted socket, creates a
fresh named volume, boots `postgres:15.7-alpine` as `postgres` with all
capabilities dropped, and asserts it reaches "ready to accept connections".
It is skipped automatically when the Docker socket is not available.
"""

import os
import time
import uuid

import pytest

DOCKER_SOCKET = "/var/run/docker.sock"
POSTGRES_IMAGE = "postgres:15.7-alpine"


def _make_client():
    import docker

    return docker.DockerClient(base_url="unix:///var/run/docker.sock")


def _docker_available() -> bool:
    if not os.path.exists(DOCKER_SOCKET):
        return False
    try:
        _make_client().ping()
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _docker_available(), reason="Docker socket is not available")
class TestDatabaseFirstBoot:
    """A fresh PostgreSQL volume must initialise as the `postgres` user."""

    def test_fresh_volume_initialises_as_postgres_user(self):
        import docker
        from docker.types import Mount

        client = _make_client()
        volume_name = f"plane-db-firstboot-{uuid.uuid4().hex[:8]}"
        container_name = f"plane-db-firstboot-{uuid.uuid4().hex[:8]}"
        container = None

        try:
            client.volumes.create(name=volume_name)
            container = client.containers.run(
                POSTGRES_IMAGE,
                detach=True,
                name=container_name,
                # Exactly what docker-compose.yml does for plane-db:
                # run as the non-root postgres user with all caps dropped.
                user="postgres",
                cap_drop=["ALL"],
                environment={
                    "POSTGRES_PASSWORD": "smoke-test-pass",
                    "POSTGRES_USER": "plane",
                    "POSTGRES_DB": "plane",
                    "PGDATA": "/var/lib/postgresql/data/pgdata",
                },
                mounts=[
                    Mount(
                        target="/var/lib/postgresql/data",
                        source=volume_name,
                        type="volume",
                    )
                ],
            )

            deadline = time.monotonic() + 90
            logs = ""
            while time.monotonic() < deadline:
                logs = container.logs(stdout=True, stderr=True).decode(
                    errors="replace"
                )
                if "database system is ready to accept connections" in logs:
                    return  # initdb + first start succeeded as non-root

                if container.status in ("exited", "dead"):
                    pytest.fail(
                        "postgres container exited during first boot as "
                        f"user 'postgres'.\nLogs:\n{logs[-3000:]}"
                    )
                time.sleep(2)

            pytest.fail(
                "postgres did not initialise a fresh volume as user 'postgres' "
                f"within 90s.\nLast logs:\n{logs[-3000:]}"
            )
        finally:
            if container is not None:
                try:
                    container.remove(force=True)
                except Exception:
                    pass
            try:
                client.volumes.get(volume_name).remove(force=True)
            except Exception:
                pass
