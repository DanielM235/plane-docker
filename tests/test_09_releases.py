"""
test_09_releases.py — Validate the versioned release configuration.

Checks that `releases/order.txt` and its per-release `.env` files are
consistent with the documented migration path, and that release notes
exist for every non-baseline step.
"""

from pathlib import Path

import pytest

PROJECT_ROOT = Path("/project")
RELEASES_DIR = PROJECT_ROOT / "releases"

EXPECTED_ORDER = ["v1.2.3", "v1.3.0", "v1.3.1", "v1.4.0", "v1.4.1"]


def _read_order() -> list[str]:
    lines = []
    for line in (RELEASES_DIR / "order.txt").read_text().splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        lines.append(s)
    return lines


class TestReleaseOrder:
    def test_order_file_exists(self):
        assert (RELEASES_DIR / "order.txt").exists()

    def test_order_matches_documented_path(self):
        assert _read_order() == EXPECTED_ORDER


class TestReleaseEnvFiles:
    @pytest.mark.parametrize("version", EXPECTED_ORDER)
    def test_release_env_file_exists(self, version):
        assert (RELEASES_DIR / f"{version}.env").exists()

    @pytest.mark.parametrize("version", EXPECTED_ORDER)
    def test_release_env_file_pins_app_release(self, version):
        content = (RELEASES_DIR / f"{version}.env").read_text()
        assert f"APP_RELEASE={version}" in content


class TestMigrationDocs:
    @pytest.mark.parametrize("version", ["v1.3.0", "v1.3.1", "v1.4.0", "v1.4.1"])
    def test_release_doc_exists(self, version):
        path = PROJECT_ROOT / "docs" / "releases" / f"{version}.md"
        assert path.exists(), f"missing release notes: {path}"

    def test_migration_guide_exists(self):
        assert (PROJECT_ROOT / "docs" / "MIGRATION.md").exists()

    def test_backup_restore_guide_exists(self):
        assert (PROJECT_ROOT / "docs" / "BACKUP_RESTORE.md").exists()
