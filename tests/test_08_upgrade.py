"""
test_08_upgrade.py — Unit tests for `./setup.sh upgrade`.

Verifies the stepwise migration v1.2.3 → v1.3.0 → v1.3.1 → v1.4.0 → v1.4.1
using the fake docker binary (never touches the real Docker daemon).
"""

import pytest

from support import docker_calls, env_value, run_setup, set_env_value


def _init_env(workspace, release="v1.2.3"):
    """Create a .env and pin it to *release*."""
    run_setup(workspace, "install")
    set_env_value(workspace, "APP_RELEASE", release)


class TestUpgrade:
    """`./setup.sh upgrade` behaviour."""

    def test_upgrade_fails_without_env(self, workspace):
        r = run_setup(workspace, "upgrade")
        assert r.returncode != 0, "upgrade without .env must fail"

    def test_upgrade_walks_full_path(self, workspace):
        _init_env(workspace)
        r = run_setup(workspace, "upgrade", "--skip-tests")
        assert r.returncode == 0, f"upgrade failed:\n{r.stderr}"
        assert env_value(workspace, "APP_RELEASE") == "v1.4.1"

    def test_upgrade_to_specific_version(self, workspace):
        _init_env(workspace)
        r = run_setup(workspace, "upgrade", "--to", "v1.3.1", "--skip-tests")
        assert r.returncode == 0, f"upgrade failed:\n{r.stderr}"
        assert env_value(workspace, "APP_RELEASE") == "v1.3.1"

    def test_upgrade_unknown_target_fails(self, workspace):
        _init_env(workspace)
        r = run_setup(workspace, "upgrade", "--to", "v9.9.9", "--skip-tests")
        assert r.returncode != 0, "unknown target version must fail"

    def test_upgrade_already_at_target_is_noop(self, workspace):
        _init_env(workspace, "v1.4.1")
        r = run_setup(workspace, "upgrade", "--skip-tests")
        assert r.returncode == 0
        assert "nothing to migrate" in (r.stdout + r.stderr).lower()

    def test_upgrade_runs_migrator_every_step(self, workspace):
        _init_env(workspace)
        log = workspace / "docker_calls.log"
        run_setup(
            workspace, "upgrade", "--skip-tests",
            extra_env={"DOCKER_LOG": str(log)},
        )
        calls = docker_calls(log)
        migrator_calls = [
            c for c in calls if "migrator" in c and "run" in c.split()
        ]
        assert len(migrator_calls) == 4, (
            f"expected one migrator run per step (4), got {len(migrator_calls)}"
        )

    def test_upgrade_backs_up_every_step(self, workspace):
        _init_env(workspace)
        run_setup(workspace, "upgrade", "--skip-tests")
        backups = list((workspace / "backups").glob("*/"))
        assert len(backups) == 4, (
            f"expected one backup per step (4), got {len(backups)}"
        )

    def test_upgrade_runs_tests_by_default(self, workspace):
        _init_env(workspace)
        log = workspace / "docker_calls.log"
        r = run_setup(
            workspace, "upgrade", "--to", "v1.3.0",
            extra_env={"DOCKER_LOG": str(log)},
        )
        assert r.returncode == 0, f"upgrade failed:\n{r.stderr}"
        calls = docker_calls(log)
        assert any("test-runner" in c for c in calls), (
            f"upgrade must run the test suite by default.\nCalls:\n{calls}"
        )

    def test_upgrade_skip_tests_disables_test_run(self, workspace):
        _init_env(workspace)
        log = workspace / "docker_calls.log"
        run_setup(
            workspace, "upgrade", "--to", "v1.3.0", "--skip-tests",
            extra_env={"DOCKER_LOG": str(log)},
        )
        calls = docker_calls(log)
        assert not any("test-runner" in c for c in calls), (
            f"--skip-tests must not run the test suite.\nCalls:\n{calls}"
        )
