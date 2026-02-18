"""
test_06_setup_sh.py — Unit / integration tests for setup.sh.

These tests exercise setup.sh in an isolated temporary directory.
They do NOT require the Plane stack to be running and they NEVER call
the real Docker daemon.  All docker / docker-compose interactions are
intercepted by a minimal bash stub placed earlier on PATH.

Layout assumptions inside the test container (set by docker-compose.test.yml):
  /project/setup.sh        — the script under test
  /project/.env.example    — template used by setup.sh's init_env()
  /project/VERSION         — config version file
"""

import os
import re
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

# ----------------------------------------------------------------
# Project-root paths (mounted read-only at /project by compose)
# ----------------------------------------------------------------
PROJECT_ROOT = Path("/project")
SETUP_SH     = PROJECT_ROOT / "setup.sh"
ENV_EXAMPLE  = PROJECT_ROOT / ".env.example"
VERSION_FILE = PROJECT_ROOT / "VERSION"

# ----------------------------------------------------------------
# Fake docker binary
#
# Handles every docker / docker-compose call that setup.sh makes:
#   docker version --format '...'   → version string for prerequisites check
#   docker compose version          → compose version string
#   docker compose ... pull         → noop (exit 0)
#   docker compose ... run ...      → noop (exit 0)
#   docker compose ... up ...       → noop (exit 0)
#   docker compose ... down         → noop (exit 0)
#   docker compose ... ps           → noop (exit 0)
# ----------------------------------------------------------------
_DOCKER_STUB = textwrap.dedent("""\
    #!/usr/bin/env bash
    # Log every invocation (all args on one line) when DOCKER_LOG is set.
    if [[ -n "${DOCKER_LOG:-}" ]]; then
        echo "$*" >> "${DOCKER_LOG}"
    fi
    if [[ "${1:-}" == "version" ]]; then
        echo "26.0.0"
        exit 0
    fi
    if [[ "${1:-}" == "compose" && "${2:-}" == "version" ]]; then
        echo "Docker Compose version v2.24.0"
        exit 0
    fi
    # All other compose sub-commands succeed silently
    exit 0
""")


# ----------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------
@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    """
    Populate a fresh temp directory with everything setup.sh needs:
      - setup.sh   (executable copy from /project)
      - .env.example
      - VERSION
      - docker-compose.yml       (empty stub — never parsed in these tests)
      - docker-compose.test.yml  (empty stub)
      - bin/docker               (fake docker binary)
    """
    # Copy project files
    shutil.copy(SETUP_SH, tmp_path / "setup.sh")
    shutil.copy(ENV_EXAMPLE, tmp_path / ".env.example")
    shutil.copy(VERSION_FILE, tmp_path / "VERSION")
    (tmp_path / "setup.sh").chmod(0o755)

    # setup.sh references COMPOSE_FILE and COMPOSE_TEST_FILE; touch them so
    # the script doesn't error on file-not-found when docker compose is called.
    (tmp_path / "docker-compose.yml").write_text("# stub\n")
    (tmp_path / "docker-compose.test.yml").write_text("# stub\n")

    # Fake docker binary
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker_stub = bin_dir / "docker"
    docker_stub.write_text(_DOCKER_STUB)
    docker_stub.chmod(0o755)

    return tmp_path


def run_setup(
    workspace: Path,
    *args: str,
    extra_env: dict | None = None,
) -> subprocess.CompletedProcess:
    """
    Execute setup.sh inside *workspace* with the fake docker on PATH.
    Returns the CompletedProcess (stdout + stderr captured, text mode).
    Never raises on non-zero exit — callers assert the exit code.

    *extra_env* is merged into the subprocess environment after the defaults,
    allowing individual tests to inject variables (e.g. DOCKER_LOG).
    """
    env = os.environ.copy()
    # Prepend our stub dir so "docker" resolves to the fake binary
    env["PATH"] = str(workspace / "bin") + ":" + env.get("PATH", "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin")
    # Prevent any ambient host variables from leaking into .env generation
    for var in ("COMPOSE_PROJECT_NAME", "DOMAIN_NAME", "LISTEN_HTTP_PORT", "WEB_URL"):
        env.pop(var, None)
    if extra_env:
        env.update(extra_env)

    return subprocess.run(
        ["bash", str(workspace / "setup.sh")] + list(args),
        cwd=str(workspace),
        env=env,
        capture_output=True,
        text=True,
    )


# ----------------------------------------------------------------
# Helper
# ----------------------------------------------------------------
def env_value(workspace: Path, key: str) -> str | None:
    """Return the value of *key* from workspace/.env, or None."""
    env_path = workspace / ".env"
    if not env_path.exists():
        return None
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1]
    return None


# ================================================================
# Tests
# ================================================================

class TestHelp:
    """help / --help / -h prints usage and exits 0."""

    @pytest.mark.parametrize("arg", ["help", "--help", "-h"])
    def test_help_exits_zero(self, workspace, arg):
        r = run_setup(workspace, arg)
        assert r.returncode == 0, f"'{arg}' must exit 0, got {r.returncode}\n{r.stderr}"

    def test_help_lists_install(self, workspace):
        r = run_setup(workspace, "help")
        assert "install" in r.stdout

    def test_help_lists_start(self, workspace):
        r = run_setup(workspace, "help")
        assert "start" in r.stdout

    def test_help_shows_project_name_option(self, workspace):
        r = run_setup(workspace, "help")
        assert "--project-name" in r.stdout

    def test_help_shows_domain_option(self, workspace):
        r = run_setup(workspace, "help")
        assert "--domain" in r.stdout

    def test_help_shows_port_option(self, workspace):
        r = run_setup(workspace, "help")
        assert "--port" in r.stdout


class TestUnknownCommand:
    """Unknown commands must fail with a non-zero exit code."""

    @pytest.mark.parametrize("cmd", ["foobar", "deploy", "run", "exec"])
    def test_unknown_command_fails(self, workspace, cmd):
        r = run_setup(workspace, cmd)
        assert r.returncode != 0, f"Unknown command '{cmd}' should exit non-zero"


class TestInstallEnvCreation:
    """install creates .env from .env.example and substitutes secrets."""

    def test_creates_env_when_missing(self, workspace):
        assert not (workspace / ".env").exists()
        r = run_setup(workspace, "install")
        assert r.returncode == 0, f"install failed:\n{r.stderr}"
        assert (workspace / ".env").exists(), "install must create .env"

    def test_no_change_me_placeholders_remain(self, workspace):
        run_setup(workspace, "install")
        content = (workspace / ".env").read_text()
        assert "CHANGE_ME_" not in content, (
            "install must replace all CHANGE_ME_ placeholders with real secrets"
        )

    def test_secret_key_is_long(self, workspace):
        """SECRET_KEY should be at least 64 hex chars (50 bytes × 2)."""
        run_setup(workspace, "install")
        value = env_value(workspace, "SECRET_KEY")
        assert value is not None, "SECRET_KEY not found in .env"
        assert len(value) >= 64, f"SECRET_KEY too short: {len(value)} chars"

    def test_secret_key_is_hex(self, workspace):
        run_setup(workspace, "install")
        value = env_value(workspace, "SECRET_KEY")
        assert value is not None
        assert re.fullmatch(r"[0-9a-f]+", value), "SECRET_KEY must be lowercase hex"

    def test_skips_env_if_already_exists(self, workspace):
        """If .env already exists install must not overwrite it."""
        sentinel = "COMPOSE_PROJECT_NAME=already-configured\n"
        (workspace / ".env").write_text(sentinel)
        run_setup(workspace, "install")
        content = (workspace / ".env").read_text()
        assert "already-configured" in content, (
            "install must not overwrite an existing .env"
        )

    def test_install_exits_zero(self, workspace):
        r = run_setup(workspace, "install")
        assert r.returncode == 0, f"install should exit 0\n{r.stderr}"


class TestInstallOptions:
    """install options override the correct .env variables."""

    def test_project_name_long_flag(self, workspace):
        run_setup(workspace, "install", "--project-name", "my-plane")
        assert env_value(workspace, "COMPOSE_PROJECT_NAME") == "my-plane"

    def test_project_name_short_flag(self, workspace):
        run_setup(workspace, "install", "-p", "my-plane-short")
        assert env_value(workspace, "COMPOSE_PROJECT_NAME") == "my-plane-short"

    def test_domain_sets_domain_name(self, workspace):
        run_setup(workspace, "install", "--domain", "acme.example.com")
        assert env_value(workspace, "DOMAIN_NAME") == "acme.example.com"

    def test_domain_sets_web_url(self, workspace):
        run_setup(workspace, "install", "-d", "acme.example.com")
        assert env_value(workspace, "WEB_URL") == "https://acme.example.com"

    def test_domain_sets_cors_allowed_origins(self, workspace):
        run_setup(workspace, "install", "--domain", "acme.example.com")
        assert env_value(workspace, "CORS_ALLOWED_ORIGINS") == "https://acme.example.com"

    def test_port_long_flag(self, workspace):
        run_setup(workspace, "install", "--port", "9099")
        assert env_value(workspace, "LISTEN_HTTP_PORT") == "9099"

    def test_port_short_flag(self, workspace):
        run_setup(workspace, "install", "-P", "9100")
        assert env_value(workspace, "LISTEN_HTTP_PORT") == "9100"

    def test_all_options_combined(self, workspace):
        run_setup(
            workspace, "install",
            "--project-name", "combo-test",
            "--domain",       "combo.example.com",
            "--port",         "8099",
        )
        assert env_value(workspace, "COMPOSE_PROJECT_NAME")  == "combo-test"
        assert env_value(workspace, "DOMAIN_NAME")           == "combo.example.com"
        assert env_value(workspace, "WEB_URL")               == "https://combo.example.com"
        assert env_value(workspace, "CORS_ALLOWED_ORIGINS")  == "https://combo.example.com"
        assert env_value(workspace, "LISTEN_HTTP_PORT")      == "8099"

    def test_option_does_not_create_duplicate_lines(self, workspace):
        """--domain must replace the existing line, not append."""
        run_setup(workspace, "install", "--domain", "nodupe.example.com")
        lines = [
            l for l in (workspace / ".env").read_text().splitlines()
            if l.startswith("DOMAIN_NAME=")
        ]
        assert len(lines) == 1, f"Expected exactly one DOMAIN_NAME= line, found: {lines}"

    def test_options_are_idempotent_with_existing_env(self, workspace):
        """Re-running install (with .env present) with options still applies overrides."""
        # First install creates .env
        run_setup(workspace, "install", "--port", "8081")
        # Second call (env exists — skips generation) applies the port override
        run_setup(workspace, "install", "--port", "8082")
        assert env_value(workspace, "LISTEN_HTTP_PORT") == "8082"


class TestInstallOptionErrors:
    """install must fail when required option values are missing or flags are unknown."""

    @pytest.mark.parametrize("flag", ["--project-name", "-p"])
    def test_project_name_missing_value(self, workspace, flag):
        r = run_setup(workspace, "install", flag)
        assert r.returncode != 0, f"'{flag}' with no value should fail"

    @pytest.mark.parametrize("flag", ["--domain", "-d"])
    def test_domain_missing_value(self, workspace, flag):
        r = run_setup(workspace, "install", flag)
        assert r.returncode != 0

    @pytest.mark.parametrize("flag", ["--port", "-P"])
    def test_port_missing_value(self, workspace, flag):
        r = run_setup(workspace, "install", flag)
        assert r.returncode != 0

    def test_unknown_option_fails(self, workspace):
        r = run_setup(workspace, "install", "--bad-option")
        assert r.returncode != 0


class TestInstallDoesNotStart:
    """
    install must touch zero containers — it only generates .env and pulls
    images.  No 'docker compose run', no 'docker compose up', no 'docker
    compose down'.  The entire container lifecycle starts with 'start'.

    This is intentional: the user must be able to inspect and edit the
    generated .env, configure nginx, and make any custom adjustments
    BEFORE the first container ever runs.

    How it works: the docker stub writes every invocation to $DOCKER_LOG so
    we can assert exactly which subcommands were (and were not) called.
    """

    # ------------------------------------------------------------------
    # Subcommands that must NOT appear during install
    # ------------------------------------------------------------------
    @pytest.mark.parametrize("forbidden", ["up", "down", "run", "start"])
    def test_install_calls_no_container_subcommand(self, workspace, forbidden):
        """
        None of the container-lifecycle subcommands (up, down, run, start)
        may be called by install.  Each is checked independently so a
        failure message names the exact offending subcommand.
        """
        log_file = workspace / "docker_calls.log"
        r = run_setup(workspace, "install", extra_env={"DOCKER_LOG": str(log_file)})
        assert r.returncode == 0, f"install failed unexpectedly:\n{r.stderr}"

        calls = log_file.read_text() if log_file.exists() else ""
        for line in calls.splitlines():
            assert forbidden not in line.split(), (
                f"install must not call 'docker compose {forbidden}', "
                f"but found: {line!r}\nFull docker call log:\n{calls}"
            )

    def test_install_does_not_call_migrator(self, workspace):
        """
        The migrator service must not be invoked during install at all.
        Migrations run as the first step of 'start', after the user has
        validated .env.
        """
        log_file = workspace / "docker_calls.log"
        r = run_setup(workspace, "install", extra_env={"DOCKER_LOG": str(log_file)})
        assert r.returncode == 0, f"install failed unexpectedly:\n{r.stderr}"

        calls = log_file.read_text() if log_file.exists() else ""
        assert not any(
            "migrator" in line for line in calls.splitlines()
        ), f"install must not call the migrator.\nFull log:\n{calls}"

    def test_install_does_call_pull(self, workspace):
        """install must call docker compose pull (image pre-fetch phase)."""
        log_file = workspace / "docker_calls.log"
        run_setup(workspace, "install", extra_env={"DOCKER_LOG": str(log_file)})

        calls = log_file.read_text() if log_file.exists() else ""
        assert any(
            "pull" in line.split() for line in calls.splitlines()
        ), f"install must call 'docker compose pull'.\nFull log:\n{calls}"


class TestStart:
    """start command behaviour."""

    def test_start_fails_without_env(self, workspace):
        """start without a .env must exit non-zero and mention install."""
        assert not (workspace / ".env").exists()
        r = run_setup(workspace, "start")
        assert r.returncode != 0
        combined = (r.stdout + r.stderr).lower()
        assert "install" in combined or ".env" in combined, (
            "Error message should mention 'install' or '.env'"
        )

    def test_start_succeeds_with_env(self, workspace):
        """start with a .env in place must exit 0."""
        (workspace / ".env").write_text(
            "COMPOSE_PROJECT_NAME=test-proj\n"
            "LISTEN_HTTP_PORT=8080\n"
        )
        r = run_setup(workspace, "start")
        assert r.returncode == 0, f"start with .env should succeed\n{r.stderr}"

    def test_start_reports_proxy_port(self, workspace):
        """start must echo the LISTEN_HTTP_PORT value so the operator knows."""
        (workspace / ".env").write_text(
            "COMPOSE_PROJECT_NAME=test-proj\n"
            "LISTEN_HTTP_PORT=9191\n"
        )
        r = run_setup(workspace, "start")
        assert "9191" in r.stdout, "start should mention the proxy port"

    def test_start_calls_migrator_before_up(self, workspace):
        """
        start must run the migrator before bringing the stack up, so that
        the database schema is always current before services connect to it.
        """
        log_file = workspace / "docker_calls.log"
        (workspace / ".env").write_text(
            "COMPOSE_PROJECT_NAME=test-proj\n"
            "LISTEN_HTTP_PORT=8080\n"
        )
        r = run_setup(workspace, "start", extra_env={"DOCKER_LOG": str(log_file)})
        assert r.returncode == 0, f"start failed:\n{r.stderr}"

        calls = log_file.read_text() if log_file.exists() else ""
        lines = calls.splitlines()

        migrator_idx = next((i for i, l in enumerate(lines) if "migrator" in l), None)
        up_idx = next((i for i, l in enumerate(lines) if "up" in l.split()), None)

        assert migrator_idx is not None, f"start must call the migrator.\nLog:\n{calls}"
        assert up_idx is not None, f"start must call 'docker compose up'.\nLog:\n{calls}"
        assert migrator_idx < up_idx, (
            f"migrator must run BEFORE 'compose up' "
            f"(migrator at {migrator_idx}, up at {up_idx})"
        )
