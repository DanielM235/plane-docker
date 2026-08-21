"""
test_07_backup.py — Unit tests for `./setup.sh backup` and `restore`.

Like test_06, these run against a fake docker binary and never touch the
real Docker daemon or the Plane stack.
"""

import pytest

from support import docker_calls, env_value, run_setup, set_env_value


class TestBackup:
    """`./setup.sh backup` behaviour."""

    def test_backup_fails_without_env(self, workspace):
        r = run_setup(workspace, "backup")
        assert r.returncode != 0, "backup without .env must fail"

    def test_backup_creates_manifest_and_artifacts(self, workspace):
        run_setup(workspace, "install")
        r = run_setup(workspace, "backup")
        assert r.returncode == 0, f"backup failed:\n{r.stderr}"

        backups = list((workspace / "backups").glob("*/"))
        assert backups, "backup must create a timestamped directory under backups/"
        b = backups[0]
        assert (b / "manifest.txt").exists()
        assert (b / "db" / "plane.dump").exists()
        assert (b / "files" / "minio-data.tgz").exists()

    def test_backup_manifest_contains_release_and_project(self, workspace):
        run_setup(workspace, "install")
        run_setup(workspace, "backup")
        manifest = next((workspace / "backups").glob("*/manifest.txt"))
        content = manifest.read_text()
        assert "app_release=" in content
        assert "project=" in content

    def test_backup_invokes_pg_dump_against_db(self, workspace):
        run_setup(workspace, "install")
        log = workspace / "docker_calls.log"
        run_setup(workspace, "backup", extra_env={"DOCKER_LOG": str(log)})
        calls = docker_calls(log)
        assert any("pg_dump" in c and "plane-db" in c for c in calls), (
            f"backup must call pg_dump against plane-db.\nCalls:\n{calls}"
        )

    def test_backup_invokes_minio_volume_tar(self, workspace):
        run_setup(workspace, "install")
        log = workspace / "docker_calls.log"
        run_setup(workspace, "backup", extra_env={"DOCKER_LOG": str(log)})
        calls = docker_calls(log)
        assert any("tar" in c and "miniodata" in c for c in calls), (
            f"backup must archive the MinIO volume.\nCalls:\n{calls}"
        )

    def test_backup_uses_custom_backup_dir(self, workspace):
        run_setup(workspace, "install")
        set_env_value(workspace, "BACKUP_DIR", "my-backups")
        run_setup(workspace, "backup")
        assert (workspace / "my-backups").is_dir(), (
            "backup must honour BACKUP_DIR"
        )

    def test_backup_copies_env_file(self, workspace):
        run_setup(workspace, "install")
        run_setup(workspace, "backup")
        b = next((workspace / "backups").glob("*/"))
        assert (b / ".env").exists(), "backup must keep a copy of .env"


class TestRestore:
    """`./setup.sh restore <dir>` behaviour."""

    def test_restore_requires_argument(self, workspace):
        run_setup(workspace, "install")
        r = run_setup(workspace, "restore")
        assert r.returncode != 0, "restore without a directory must fail"

    def test_restore_missing_dir_fails(self, workspace):
        run_setup(workspace, "install")
        r = run_setup(workspace, "restore", "does-not-exist")
        assert r.returncode != 0, "restore of a missing directory must fail"

    def test_restore_invokes_pg_restore_and_tar(self, workspace):
        run_setup(workspace, "install")
        run_setup(workspace, "backup")
        b = next((workspace / "backups").glob("*/"))

        log = workspace / "docker_calls.log"
        r = run_setup(workspace, "restore", str(b), extra_env={"DOCKER_LOG": str(log)})
        assert r.returncode == 0, f"restore failed:\n{r.stderr}"

        calls = docker_calls(log)
        assert any("pg_restore" in c for c in calls), (
            f"restore must call pg_restore.\nCalls:\n{calls}"
        )
        assert any("tar" in c and "xzf" in c for c in calls), (
            f"restore must extract the MinIO archive.\nCalls:\n{calls}"
        )
